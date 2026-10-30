"""Planning use cases. Authorization enforced here.

Company and department plans are set by the director (ALL scope); a head of sales
(TEAM) distributes them to managers (USER scope). Absences are managed by the head
of sales or the director.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal, Scope
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.planning.application.ports import (
    AbsenceFilter,
    AbsenceRepository,
    PlanFilter,
    PlanRepository,
)
from modules.planning.domain.absence import Absence, AbsenceKind
from modules.planning.domain.plan import Plan, PlanChange, PlanMetric, PlanScope
from shared.application.ports import UnitOfWork
from shared.domain.errors import AccessDeniedError, NotFoundError


@dataclass(frozen=True, slots=True)
class SetPlan:
    scope: PlanScope
    scope_id: UUID | None
    metric: PlanMetric
    period: str
    target: Decimal


@dataclass(frozen=True, slots=True)
class CreateAbsence:
    user_id: UUID
    kind: AbsenceKind
    start_date: date
    end_date: date
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class PlanPage:
    items: list[Plan]
    total: int


@dataclass(frozen=True, slots=True)
class AbsencePage:
    items: list[Absence]
    total: int


class PlanningService:
    def __init__(
        self,
        plans: PlanRepository,
        absences: AbsenceRepository,
        uow: UnitOfWork,
        audit: AuditLog,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._plans = plans
        self._absences = absences
        self._uow = uow
        self._audit = audit
        self._policy = policy or AccessPolicy()

    # --- plans --------------------------------------------------------------

    def set_plan(self, principal: Principal, command: SetPlan) -> Plan:
        scope = self._policy.require(principal, Permission.PLANS_MANAGE)
        # Company/department targets are the director's; user targets may also be
        # set by a head of sales (TEAM scope).
        if command.scope in (PlanScope.COMPANY, PlanScope.DEPARTMENT) and scope is not Scope.ALL:
            raise AccessDeniedError("Only a director sets company or department plans.")
        with self._uow:
            existing = self._plans.find(
                command.scope, command.scope_id, command.metric, command.period
            )
            if existing is None:
                plan = Plan.create(
                    scope=command.scope,
                    scope_id=command.scope_id,
                    metric=command.metric,
                    period=command.period,
                    target=command.target,
                )
                self._plans.add(plan)
                self._plans.add_change(
                    PlanChange.record(
                        plan_id=plan.id,
                        author_user_id=principal.user_id,
                        old_target=None,
                        new_target=plan.target,
                    )
                )
                self._record(principal, "plan.created", plan.id)
                return plan
            old_target = existing.target
            if existing.set_target(command.target):
                self._plans.save(existing)
                self._plans.add_change(
                    PlanChange.record(
                        plan_id=existing.id,
                        author_user_id=principal.user_id,
                        old_target=old_target,
                        new_target=existing.target,
                    )
                )
                self._record(principal, "plan.updated", existing.id)
            return existing

    def list_plans(
        self, principal: Principal, filters: PlanFilter, *, limit: int, offset: int
    ) -> PlanPage:
        scope = self._policy.require(principal, Permission.PLANS_VIEW)
        # A manager (OWN scope) only sees their own user plans.
        if scope is Scope.OWN:
            filters = PlanFilter(
                scope=PlanScope.USER,
                scope_id=principal.user_id,
                metric=filters.metric,
                period=filters.period,
            )
        return PlanPage(
            items=self._plans.list_all(filters, limit=limit, offset=offset),
            total=self._plans.count(filters),
        )

    def plan_history(self, principal: Principal, plan_id: UUID) -> list[PlanChange]:
        self._policy.require(principal, Permission.PLANS_VIEW)
        if self._plans.get(plan_id) is None:
            raise NotFoundError("Plan not found.")
        return self._plans.list_changes(plan_id)

    # --- absences -----------------------------------------------------------

    def create_absence(self, principal: Principal, command: CreateAbsence) -> Absence:
        self._policy.require(principal, Permission.ABSENCES_MANAGE)
        absence = Absence.create(
            user_id=command.user_id,
            kind=command.kind,
            start_date=command.start_date,
            end_date=command.end_date,
            created_by=principal.user_id,
            comment=command.comment,
            team_id=principal.department_id,
        )
        with self._uow:
            self._absences.add(absence)
            self._record(principal, "absence.created", absence.id)
        return absence

    def list_absences(
        self, principal: Principal, filters: AbsenceFilter, *, limit: int, offset: int
    ) -> AbsencePage:
        self._policy.require(principal, Permission.ABSENCES_MANAGE)
        return AbsencePage(
            items=self._absences.list_all(filters, limit=limit, offset=offset),
            total=self._absences.count(filters),
        )

    # --- helpers ------------------------------------------------------------

    def _record(self, principal: Principal, action: str, resource_id: UUID) -> None:
        resource_type = "plan" if action.startswith("plan") else "absence"
        self._audit.record(
            AuditEntry(
                actor_user_id=principal.user_id,
                action=action,
                resource_type=resource_type,
                resource_id=str(resource_id),
            )
        )
