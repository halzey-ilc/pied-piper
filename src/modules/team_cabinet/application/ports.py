from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from modules.team_cabinet.domain.board import Member, RawDay


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
