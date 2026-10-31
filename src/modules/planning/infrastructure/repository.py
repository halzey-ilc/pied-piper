from __future__ import annotations

from uuid import UUID

from django.db.models import QuerySet

from modules.planning.application.ports import AbsenceFilter, PlanFilter
from modules.planning.domain.absence import Absence, AbsenceKind
from modules.planning.domain.plan import Plan, PlanChange, PlanMetric, PlanScope
from modules.planning.infrastructure.models import AbsenceRecord, PlanChangeRecord, PlanRecord


def _plan_to_domain(row: PlanRecord) -> Plan:
    return Plan(
        id=row.id,
        scope=PlanScope(row.scope),
        scope_id=row.scope_id,
        metric=PlanMetric(row.metric),
        period=row.period,
        target=row.target,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _change_to_domain(row: PlanChangeRecord) -> PlanChange:
    return PlanChange(
        id=row.id,
        plan_id=row.plan_id,
        author_user_id=row.author_user_id,
        old_target=row.old_target,
        new_target=row.new_target,
        created_at=row.created_at,
    )


def _absence_to_domain(row: AbsenceRecord) -> Absence:
    return Absence(
        id=row.id,
        user_id=row.user_id,
        kind=AbsenceKind(row.kind),
        start_date=row.start_date,
        end_date=row.end_date,
        created_by=row.created_by,
        comment=row.comment,
        team_id=row.team_id,
        created_at=row.created_at,
    )


class DjangoPlanRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def _qs(self) -> QuerySet[PlanRecord]:
        return PlanRecord.objects.using(self.using)

    def _filtered(self, filters: PlanFilter) -> QuerySet[PlanRecord]:
        qs = self._qs()
        if filters.scope is not None:
            qs = qs.filter(scope=filters.scope.value)
        if filters.scope_id is not None:
            qs = qs.filter(scope_id=filters.scope_id)
        if filters.metric is not None:
            qs = qs.filter(metric=filters.metric.value)
        if filters.period is not None:
            qs = qs.filter(period=filters.period)
        return qs

    def add(self, plan: Plan) -> None:
        self._qs().create(
            id=plan.id,
            scope=plan.scope.value,
            scope_id=plan.scope_id,
            metric=plan.metric.value,
            period=plan.period,
            target=plan.target,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
        )

    def get(self, plan_id: UUID) -> Plan | None:
        row = self._qs().filter(id=plan_id).first()
        return None if row is None else _plan_to_domain(row)

    def find(
        self, scope: PlanScope, scope_id: UUID | None, metric: PlanMetric, period: str
    ) -> Plan | None:
        row = (
            self._qs()
            .filter(scope=scope.value, scope_id=scope_id, metric=metric.value, period=period)
            .first()
        )
        return None if row is None else _plan_to_domain(row)

    def save(self, plan: Plan) -> None:
        self._qs().filter(id=plan.id).update(target=plan.target, updated_at=plan.updated_at)

    def list_all(self, filters: PlanFilter, *, limit: int, offset: int) -> list[Plan]:
        rows = self._filtered(filters).order_by("-period", "scope")[offset : offset + limit]
        return [_plan_to_domain(row) for row in rows]

    def count(self, filters: PlanFilter) -> int:
        return self._filtered(filters).count()

    def add_change(self, change: PlanChange) -> None:
        PlanChangeRecord.objects.using(self.using).create(
            id=change.id,
            plan_id=change.plan_id,
            author_user_id=change.author_user_id,
            old_target=change.old_target,
            new_target=change.new_target,
            created_at=change.created_at,
        )

    def list_changes(self, plan_id: UUID) -> list[PlanChange]:
        rows = (
            PlanChangeRecord.objects.using(self.using)
            .filter(plan_id=plan_id)
            .order_by("created_at", "id")
        )
        return [_change_to_domain(row) for row in rows]


class DjangoAbsenceRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def _qs(self) -> QuerySet[AbsenceRecord]:
        return AbsenceRecord.objects.using(self.using)

    def _filtered(self, filters: AbsenceFilter) -> QuerySet[AbsenceRecord]:
        qs = self._qs()
        if filters.user_id is not None:
            qs = qs.filter(user_id=filters.user_id)
        if filters.kind is not None:
            qs = qs.filter(kind=filters.kind.value)
        # Overlap: absence.start <= active_to and absence.end >= active_from.
        if filters.active_to is not None:
            qs = qs.filter(start_date__lte=filters.active_to)
        if filters.active_from is not None:
            qs = qs.filter(end_date__gte=filters.active_from)
        return qs

    def add(self, absence: Absence) -> None:
        self._qs().create(
            id=absence.id,
            user_id=absence.user_id,
            kind=absence.kind.value,
            start_date=absence.start_date,
            end_date=absence.end_date,
            comment=absence.comment,
            created_by=absence.created_by,
            team_id=absence.team_id,
            created_at=absence.created_at,
        )

    def get(self, absence_id: UUID) -> Absence | None:
        row = self._qs().filter(id=absence_id).first()
        return None if row is None else _absence_to_domain(row)

    def list_all(self, filters: AbsenceFilter, *, limit: int, offset: int) -> list[Absence]:
        rows = self._filtered(filters).order_by("-start_date", "id")[offset : offset + limit]
        return [_absence_to_domain(row) for row in rows]

    def count(self, filters: AbsenceFilter) -> int:
        return self._filtered(filters).count()
