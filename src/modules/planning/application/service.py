"""Planning use cases. Authorization enforced here.

Company and department plans are set by the director (ALL scope); a head of sales
(TEAM) distributes them to the managers of their own department (USER scope) and sees
only the company plan, their department's plan and its members' plans. Absences are
managed by the director, or by a head of sales for their own department.
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
    DepartmentRoster,
    PlanFilter,
    PlanRepository,
    PlanVisibility,
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
        roster: DepartmentRoster,
        uow: UnitOfWork,
        audit: AuditLog,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._plans = plans
        self._absences = absences
        self._roster = roster
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
        if command.scope is PlanScope.USER and scope is not Scope.ALL:
            self._require_in_department(principal, command.scope_id)
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
        elif scope is Scope.TEAM:
            filters = PlanFilter(
                scope=filters.scope,
                scope_id=filters.scope_id,
                metric=filters.metric,
                period=filters.period,
                visibility=self._team_plans(principal),
            )
        return PlanPage(
            items=self._plans.list_all(filters, limit=limit, offset=offset),
            total=self._plans.count(filters),
        )

    def plan_history(self, principal: Principal, plan_id: UUID) -> list[PlanChange]:
        scope = self._policy.require(principal, Permission.PLANS_VIEW)
        plan = self._plans.get(plan_id)
        # A plan outside the caller's scope is indistinguishable from a missing one.
        if plan is None or not self._plan_visible(principal, scope, plan):
            raise NotFoundError("Plan not found.")
        return self._plans.list_changes(plan_id)

    # --- absences -----------------------------------------------------------

    def create_absence(self, principal: Principal, command: CreateAbsence) -> Absence:
        scope = self._policy.require(principal, Permission.ABSENCES_MANAGE)
        if scope is not Scope.ALL:
            self._require_in_department(principal, command.user_id)
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
        scope = self._policy.require(principal, Permission.ABSENCES_MANAGE)
        if scope is not Scope.ALL:
            filters = AbsenceFilter(
                user_id=filters.user_id,
                kind=filters.kind,
                active_from=filters.active_from,
                active_to=filters.active_to,
                user_ids=self._roster.members_of(principal.department_id),
            )
        return AbsencePage(
            items=self._absences.list_all(filters, limit=limit, offset=offset),
            total=self._absences.count(filters),
        )

    # --- helpers ------------------------------------------------------------

    def _team_plans(self, principal: Principal) -> PlanVisibility:
        return PlanVisibility(
            department_id=principal.department_id,
            user_ids=self._roster.members_of(principal.department_id),
        )

    def _plan_visible(self, principal: Principal, scope: Scope, plan: Plan) -> bool:
        if scope is Scope.ALL:
            return True
        if scope is Scope.OWN:
            return plan.scope is PlanScope.USER and plan.scope_id == principal.user_id
        visibility = self._team_plans(principal)
        if plan.scope is PlanScope.COMPANY:
            return True
        if plan.scope is PlanScope.DEPARTMENT:
            return plan.scope_id is not None and plan.scope_id == visibility.department_id
        return plan.scope_id in visibility.user_ids

    def _require_in_department(self, principal: Principal, user_id: UUID | None) -> None:
        if user_id is None or user_id not in self._roster.members_of(principal.department_id):
            raise AccessDeniedError("You can only manage members of your own department.")

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
