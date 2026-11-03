from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from modules.team_cabinet.domain.analytics import (
    LeadResponse,
    MeetingReasons,
    ReasonCount,
    StageStat,
)
from modules.team_cabinet.domain.board import Member, RawDay
from modules.team_cabinet.domain.period import Discipline, PeriodMetrics


class TeamRoster(Protocol):
    """Lists the active members of a company (optionally one department) from the
    control plane, so the board shows every manager — even those with no activity."""

    def members(self, tenant_id: UUID, department_id: UUID | None) -> list[Member]: ...


class CabinetReader(Protocol):
    """Reads today's aggregates per manager from the tenant's data stores."""

    def day_metrics(
        self,
        user_ids: list[UUID],
        *,
        day_start: datetime,
        day_end: datetime,
        now: datetime,
        outcome_cutoff: datetime,
        morning_cutoff: datetime,
        today: date,
    ) -> dict[UUID, RawDay]: ...

    def absent_user_ids(self, user_ids: list[UUID], day: date) -> set[UUID]: ...

    def monthly_sales_targets(self, user_ids: list[UUID], month: str) -> dict[UUID, Decimal]:
        """Each manager's monthly_sales plan target for the period (user-scope plans)."""
        ...

    def monthly_received(
        self, user_ids: list[UUID], month_start: date, month_end: date
    ) -> dict[UUID, Decimal]:
        """Signed received money per seller in the month (refunds subtracted)."""
        ...

    def monthly_sales_counts(
        self, user_ids: list[UUID], month_start: date, month_end: date
    ) -> dict[UUID, int]:
        """Per seller: deals whose first non-refund payment fell in the month and was
        taken by that seller (a sale is credited to the first payment's seller)."""
        ...

    def department_sales_target(self, department_id: UUID, month: str) -> Decimal | None:
        """The department's monthly_sales plan target (department-scope plan), if set."""
        ...

    def period_metrics(
        self, user_ids: list[UUID], start: date, end: date, *, tz_name: str
    ) -> dict[UUID, PeriodMetrics]:
        """Per-user figures over company-local days [start, end]."""
        ...

    def discipline(
        self, user_ids: list[UUID], *, now: datetime, today: date
    ) -> dict[UUID, Discipline]:
        """Current CRM tails per user."""
        ...


class AnalyticsReader(Protocol):
    """Funnel, losses and history read from the tenant's records."""

    def pipeline_for(
        self, pipeline_id: UUID | None, department_id: UUID | None
    ) -> tuple[UUID, str, UUID | None] | None: ...

    def funnel_stages(
        self,
        pipeline_id: UUID,
        owner_ids: list[UUID] | None,
        *,
        period: tuple[datetime, datetime],
        previous: tuple[datetime, datetime],
        now: datetime,
        stale_days: int,
    ) -> tuple[list[StageStat], dict[str, int]]: ...

    def lost_reasons(
        self, owner_ids: list[UUID] | None, start: datetime, end: datetime, tz: ZoneInfo
    ) -> tuple[list[ReasonCount], int]: ...

    def lost_count(self, owner_ids: list[UUID] | None, start: datetime, end: datetime) -> int: ...

    def meeting_reasons(
        self, owner_ids: list[UUID] | None, start: datetime, end: datetime, tz: ZoneInfo
    ) -> MeetingReasons: ...

    def meeting_counts(
        self, owner_ids: list[UUID] | None, start: datetime, end: datetime
    ) -> tuple[int, int]: ...

    def lead_response(
        self, owner_ids: list[UUID] | None, start: datetime, end: datetime
    ) -> LeadResponse: ...

    def tasks_as_of(self, user_id: UUID, day_end: datetime, day: date) -> tuple[int, int]: ...
