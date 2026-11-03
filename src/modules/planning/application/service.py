"""Planning use cases. Authorization enforced here.

Company and department plans are set by the director (ALL scope); a head of sales
(TEAM) distributes them to the managers of their own department (USER scope) and sees
only the company plan, their department's plan and its members' plans. Absences are
managed by the director, or by a head of sales for their own department.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal, Scope
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.planning.application.ports import (
    AbsenceFilter,
    AbsenceRepository,
    DepartmentRoster,
    PlanActuals,
    PlanFilter,
    PlanRepository,
    PlanVisibility,
)
from modules.planning.domain.absence import Absence, AbsenceKind
from modules.planning.domain.plan import Plan, PlanChange, PlanMetric, PlanScope, period_dates
from shared.application.ports import UnitOfWork
from shared.domain.errors import AccessDeniedError, DomainValidationError, NotFoundError


@dataclass(frozen=True, slots=True)
class SetPlan:
    scope: PlanScope
    scope_id: UUID | None
    metric: PlanMetric
    period: str
    target: Decimal
    # Money plans only; None = the company currency (or the plan's current one).
    currency: str | None = None


@dataclass(frozen=True, slots=True)
class PlanPart:
    department_id: UUID
    target: Decimal


@dataclass(frozen=True, slots=True)
class DistributePlan:
    """Split the company plan of (metric, period) between departments: the parts must add
    up to the company target. Department plans change only through this explicit call."""

    metric: PlanMetric
    period: str
    parts: tuple[PlanPart, ...]


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
class PlanProgress:
    plan: Plan
    start: date
    end: date
    actual: Decimal | None  # None: not computable from the records the CRM keeps
    percent: Decimal | None  # actual / target * 100, one decimal; None if target is 0
    remaining: Decimal | None  # target - actual, never below 0; None without an actual
    # Cumulative actual per day up to today (only when requested): (day, actual so far).
    daily: tuple[tuple[date, Decimal], ...] = ()


@dataclass(frozen=True, slots=True)
class ProgressPage:
    items: list[PlanProgress]
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
        actuals: PlanActuals | None = None,
        currency: str = "KGS",
        timezone: str = "Asia/Bishkek",
        policy: AccessPolicy | None = None,
    ) -> None:
        self._actuals = actuals
        self._currency = currency
        self._timezone = timezone
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
        if command.scope is PlanScope.DEPARTMENT and (
            command.scope_id not in self._roster.active_departments()
        ):
            raise NotFoundError("Department not found.")
        with self._uow:
            return self._upsert(principal, command)

    def distribute_plan(self, principal: Principal, command: DistributePlan) -> list[Plan]:
        """Director only: set every listed department's plan in one transaction, provided
        the parts add up exactly to the company plan of the same metric and period. Each
        department plan keeps its own change history; the money currency follows the
        company plan."""
        scope = self._policy.require(principal, Permission.PLANS_MANAGE)
        if scope is not Scope.ALL:
            raise AccessDeniedError("Only a director distributes the company plan.")
        company = self._plans.find(PlanScope.COMPANY, None, command.metric, command.period)
        if company is None:
            raise DomainValidationError("Set the company plan for this period first.")
        departments = self._roster.active_departments()
        ids = [part.department_id for part in command.parts]
        if len(set(ids)) != len(ids):
            raise DomainValidationError("Each department may appear only once.")
        if any(department_id not in departments for department_id in ids):
            raise NotFoundError("Department not found.")
        if any(part.target < Decimal(0) for part in command.parts):
            raise DomainValidationError("Plan target must not be negative.")
        total = sum((part.target for part in command.parts), Decimal(0))
        if total != company.target:
            raise DomainValidationError(
                f"The parts add up to {total}, the company plan is {company.target}."
            )
        with self._uow:
            return [
                self._upsert(
                    principal,
                    SetPlan(
                        scope=PlanScope.DEPARTMENT,
                        scope_id=part.department_id,
                        metric=command.metric,
                        period=command.period,
                        target=part.target,
                        currency=company.currency,
                    ),
                )
                for part in command.parts
            ]

    def _upsert(self, principal: Principal, command: SetPlan) -> Plan:
        """Create the plan or change its target/currency, recording history (inside a UoW)."""
        existing = self._plans.find(command.scope, command.scope_id, command.metric, command.period)
        if existing is None:
            plan = Plan.create(
                scope=command.scope,
                scope_id=command.scope_id,
                metric=command.metric,
                period=command.period,
                target=command.target,
                currency=command.currency or self._currency,
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
        if existing.set_target(command.target, command.currency):
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

    def progress(
        self,
        principal: Principal,
        filters: PlanFilter,
        *,
        limit: int,
        offset: int,
        daily_until: date | None = None,
    ) -> ProgressPage:
        """Plan vs actual for the plans the caller may see (same scope rules as the list).

        A manager sees only their own plans' progress, a head of sales the company plan,
        their department's and its members', the director everything.
        """
        page = self.list_plans(principal, filters, limit=limit, offset=offset)
        return ProgressPage(
            items=[self._progress(plan, daily_until) for plan in page.items], total=page.total
        )

    def _actual(self, plan: Plan, start: date, end: date) -> Decimal | None:
        if self._actuals is None:
            return None
        return self._actuals.actual(
            plan.metric,
            start,
            end,
            user_ids=self._contributors(plan),
            currency=plan.currency or self._currency,
            timezone=self._timezone,
        )

    def _progress(self, plan: Plan, daily_until: date | None = None) -> PlanProgress:
        start, end = period_dates(plan.metric, plan.period)
        actual = self._actual(plan, start, end)
        daily: list[tuple[date, Decimal]] = []
        if daily_until is not None and actual is not None and self._actuals is not None:
            by_day = self._actuals.actual_by_day(
                plan.metric,
                start,
                end,
                user_ids=self._contributors(plan),
                currency=plan.currency or self._currency,
                timezone=self._timezone,
            )
            running = Decimal(0)
            day = start
            while day <= min(end, daily_until):
                running += by_day.get(day, Decimal(0))
                daily.append((day, running))
                day += timedelta(days=1)
        percent = (
            (actual / plan.target * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            if actual is not None and plan.target > 0
            else None
        )
        remaining = max(plan.target - actual, Decimal(0)) if actual is not None else None
        return PlanProgress(
            plan=plan,
            start=start,
            end=end,
            actual=actual,
            percent=percent,
            remaining=remaining,
            daily=tuple(daily),
        )

    def _contributors(self, plan: Plan) -> frozenset[UUID] | None:
        """Whose results count: the manager, the department's current members, or everyone."""
        if plan.scope is PlanScope.USER and plan.scope_id is not None:
            return frozenset({plan.scope_id})
        if plan.scope is PlanScope.DEPARTMENT:
            return self._roster.members_of(plan.scope_id)
        return None

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
