from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID

from modules.planning.domain.absence import Absence, AbsenceKind
from modules.planning.domain.plan import Plan, PlanChange, PlanMetric, PlanScope


@dataclass(frozen=True, slots=True)
class PlanFilter:
    scope: PlanScope | None = None
    scope_id: UUID | None = None
    metric: PlanMetric | None = None
    period: str | None = None
    # Row limit from the caller's scope; None = every plan (director).
    visibility: PlanVisibility | None = None


@dataclass(frozen=True, slots=True)
class PlanVisibility:
    """Plans a head of sales may see: the company plan, their department's and its members'."""

    department_id: UUID | None
    user_ids: frozenset[UUID]


@dataclass(frozen=True, slots=True)
class AbsenceFilter:
    user_id: UUID | None = None
    # Row limit from the caller's scope; None = every absence (director).
    user_ids: frozenset[UUID] | None = None
    kind: AbsenceKind | None = None
    # Overlap window: absences that intersect [active_from, active_to].
    active_from: date | None = None
    active_to: date | None = None


class PlanRepository(Protocol):
    def add(self, plan: Plan) -> None: ...

    def get(self, plan_id: UUID) -> Plan | None: ...

    def find(
        self, scope: PlanScope, scope_id: UUID | None, metric: PlanMetric, period: str
    ) -> Plan | None: ...

    def save(self, plan: Plan) -> None: ...

    def list_all(self, filters: PlanFilter, *, limit: int, offset: int) -> list[Plan]: ...

    def count(self, filters: PlanFilter) -> int: ...

    def add_change(self, change: PlanChange) -> None: ...

    def list_changes(self, plan_id: UUID) -> list[PlanChange]: ...


class AbsenceRepository(Protocol):
    def add(self, absence: Absence) -> None: ...

    def get(self, absence_id: UUID) -> Absence | None: ...

    def list_all(self, filters: AbsenceFilter, *, limit: int, offset: int) -> list[Absence]: ...

    def count(self, filters: AbsenceFilter) -> int: ...


class DepartmentRoster(Protocol):
    """Active members of a department (a head of sales manages only these)."""

    def members_of(self, department_id: UUID | None) -> frozenset[UUID]: ...
