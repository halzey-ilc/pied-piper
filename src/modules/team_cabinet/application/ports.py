from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from modules.team_cabinet.domain.actions import ActionGroup
from modules.team_cabinet.domain.analytics import (
    LeadResponse,
    MeetingReasons,
    ReasonCount,
    StageStat,
)
from modules.team_cabinet.domain.board import Member, RawDay
from modules.team_cabinet.domain.my_month import MeetingsMonth, MonthMoney, SoldDeal
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


class ActionsReader(Protocol):
    """Reads the «Что делать сегодня» groups. ``owners`` limits the records to those owned
    (assigned, sold) by these users; None means the whole company. Each method returns the
    full count, counts per manager and at most ``limit`` items, oldest problems first."""

    def stalled_deal_days(self) -> int:
        """The company's stalled-deal threshold (shared with the operations rules)."""
        ...

    def payments_to_verify(
        self, owners: list[UUID] | None, *, now: datetime, limit: int
    ) -> ActionGroup: ...

    def overdue_tasks(
        self, owners: list[UUID] | None, *, today: date, limit: int, skip_misses: bool = False
    ) -> ActionGroup:
        """``skip_misses`` leaves out «Работа над ошибками» tasks (their own group)."""
        ...

    def miss_tasks(self, owners: list[UUID] | None, *, today: date, limit: int) -> ActionGroup:
        """Open «Работа над ошибками» tasks (task type review), nearest due date first."""
        ...

    def debts_to_collect(
        self, owners: list[UUID] | None, *, now: datetime, limit: int
    ) -> ActionGroup:
        """Open or won deals with confirmed money (deal currency) below the deal amount."""
        ...

    def tasks_due_soon(
        self, owners: list[UUID] | None, *, today: date, limit: int
    ) -> ActionGroup: ...

    def meetings_without_outcome(
        self, owners: list[UUID] | None, *, now: datetime, limit: int
    ) -> ActionGroup: ...

    def deals_without_next_step(
        self, owners: list[UUID] | None, *, now: datetime, limit: int
    ) -> ActionGroup: ...

    def deals_to_push(
        self, owners: list[UUID] | None, *, now: datetime, stalled_days: int, limit: int
    ) -> ActionGroup: ...

    def leads_without_contact(
        self, owners: list[UUID] | None, *, now: datetime, response_minutes: int, limit: int
    ) -> ActionGroup: ...

    def on_shift(
        self, user_ids: list[UUID], *, day_start: datetime, day_end: datetime
    ) -> set[UUID]:
        """Users with a work session started today or still open."""
        ...


class MyMonthReader(Protocol):
    """One member's own month: sales, money, meetings, pay terms and catalog products.
    Money is in the company currency only."""

    def sold_deals(self, user_id: UUID, start: date, end: date) -> list[SoldDeal]: ...

    def sales_by_product(self, user_id: UUID, start: date, end: date) -> dict[UUID | None, int]: ...

    def new_clients_count(self, user_id: UUID, start: date, end: date) -> int: ...

    def money(
        self, user_id: UUID, start: date, end: date
    ) -> tuple[MonthMoney, dict[UUID, MonthMoney]]: ...

    def meetings(
        self, user_id: UUID, start: datetime, end: datetime, now: datetime
    ) -> MeetingsMonth: ...

    def targets(self, user_id: UUID, month: str) -> tuple[Decimal | None, Decimal | None]: ...

    def pay_terms(self, user_id: UUID) -> tuple[Decimal, Decimal] | None: ...

    def shown_products(self) -> list[tuple[UUID, str]]: ...
