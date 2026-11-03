"""Lead routing settings and the pick made when a lead is captured.

Who configures: the director (staff.manage with company scope) — any department; the head
of sales (team scope) — only their own department, and cannot choose the company intake
department. Routing is off for every department until someone turns it on.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal, Role, Scope
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.leads.application.ports import LeadRouting
from modules.leads.domain.routing import Candidate, RoutingRule, candidates, next_assignee
from shared.application.ports import UnitOfWork
from shared.domain.errors import AccessDeniedError, DomainValidationError, NotFoundError

# Members whose own leads stay theirs: sales managers work what they capture, the lead
# caller (лидоруб) qualifies what they capture before handing it over.
NOT_ROUTED_ROLES = frozenset({Role.SALES_REP, Role.LEAD_CALLER})


@dataclass(frozen=True, slots=True)
class RoutingSettings:
    rule: RoutingRule
    candidates: list[Candidate]
    is_default: bool  # nothing saved yet for the department


@dataclass(frozen=True, slots=True)
class UpdateRouting:
    department_id: UUID
    enabled: bool
    member_ids: frozenset[UUID]
    require_on_shift: bool
    skip_on_absence: bool
    is_company_intake: bool | None = None  # director only; None keeps the stored value


@dataclass(frozen=True, slots=True)
class RoutingPick:
    department_id: UUID
    user_id: UUID | None  # None: nobody eligible right now


def current_candidates(routing: LeadRouting, rule: RoutingRule) -> list[Candidate]:
    managers = routing.managers(rule.department_id)
    on_shift = routing.on_shift(managers) if rule.require_on_shift else set()
    absent = routing.absent_today(managers) if rule.skip_on_absence else set()
    return candidates(rule, managers, on_shift, absent)


def pick_assignee(routing: LeadRouting, principal: Principal) -> RoutingPick | None:
    """Within the caller's transaction: who gets a lead the principal is capturing.

    None — no routing applies (the caller is a manager, or routing is off). The rotation
    pointer moves under a row lock, so concurrent captures never pick the same turn."""
    if principal.role in NOT_ROUTED_ROLES:
        return None
    department_id = principal.department_id or routing.intake_department()
    if department_id is None:
        return None
    rule = routing.rule_for_update(department_id)
    if rule is None or not rule.enabled:
        return None
    eligible = [c.user_id for c in current_candidates(routing, rule) if c.eligible(rule)]
    user_id = next_assignee(eligible, rule.last_assigned_user_id)
    if user_id is not None:
        routing.remember(department_id, user_id)
    return RoutingPick(department_id=department_id, user_id=user_id)


class LeadRoutingService:
    def __init__(
        self,
        routing: LeadRouting,
        uow: UnitOfWork,
        audit: AuditLog,
        *,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._routing = routing
        self._uow = uow
        self._audit = audit
        self._policy = policy or AccessPolicy()

    def get(self, principal: Principal, department_id: UUID | None) -> RoutingSettings:
        department_id = self._check_scope(principal, department_id)
        stored = self._routing.rule(department_id)
        rule = stored or RoutingRule(department_id=department_id)
        return RoutingSettings(
            rule=rule,
            candidates=current_candidates(self._routing, rule),
            is_default=stored is None,
        )

    def save(self, principal: Principal, command: UpdateRouting) -> RoutingSettings:
        department_id = self._check_scope(principal, command.department_id)
        managers = set(self._routing.managers(department_id))
        unknown = command.member_ids - managers
        if unknown:
            raise DomainValidationError(
                "member_ids must be active sales managers of the department."
            )
        with self._uow:
            stored = self._routing.rule_for_update(department_id)
            current = stored or RoutingRule(department_id=department_id)
            intake = current.is_company_intake
            if command.is_company_intake is not None and command.is_company_intake != intake:
                if self._policy.require(principal, Permission.STAFF_MANAGE) is not Scope.ALL:
                    raise AccessDeniedError("Only the director chooses the intake department.")
                intake = command.is_company_intake
            saved = self._routing.save_rule(
                replace(
                    current,
                    enabled=command.enabled,
                    member_ids=command.member_ids,
                    require_on_shift=command.require_on_shift,
                    skip_on_absence=command.skip_on_absence,
                    is_company_intake=intake,
                ),
                updated_by=principal.user_id,
            )
            self._audit.record(
                AuditEntry(
                    actor_user_id=principal.user_id,
                    action="lead_routing.updated",
                    resource_type="lead_routing",
                    resource_id=str(department_id),
                    changed_fields=_changed(current, saved),
                )
            )
        return RoutingSettings(
            rule=saved,
            candidates=current_candidates(self._routing, saved),
            is_default=False,
        )

    def _check_scope(self, principal: Principal, department_id: UUID | None) -> UUID:
        scope = self._policy.require(principal, Permission.STAFF_MANAGE)
        if scope is Scope.ALL:
            if department_id is None:
                raise DomainValidationError("department_id is required.")
            if not self._routing.department_exists(department_id):
                raise NotFoundError("Department not found.")
            return department_id
        # A head of sales configures only their own department.
        own = principal.department_id
        if own is None or (department_id is not None and department_id != own):
            raise AccessDeniedError("You can configure lead routing of your own department only.")
        return own


def _changed(before: RoutingRule, after: RoutingRule) -> tuple[str, ...]:
    names = ("enabled", "member_ids", "require_on_shift", "skip_on_absence", "is_company_intake")
    return tuple(n for n in names if getattr(before, n) != getattr(after, n))
