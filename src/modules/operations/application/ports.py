from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from modules.operations.domain.rules import (
    DealSnapshot,
    LeadSnapshot,
    TaskSnapshot,
    Thresholds,
)


@dataclass(frozen=True, slots=True)
class Visibility:
    """Which records the principal may see. ``owner_user_id=None`` means all."""

    owner_user_id: UUID | None = None
    team_ids: frozenset[UUID] = field(default_factory=frozenset)


class OperationsReader(Protocol):
    """Read-only access to the records the rules look at (tenant database)."""

    def stale_lead_candidates(
        self, visibility: Visibility, updated_before: datetime
    ) -> list[LeadSnapshot]:
        """New/qualified leads not updated since ``updated_before``."""
        ...

    def open_deals(self, visibility: Visibility, today: date) -> list[DealSnapshot]:
        """Open deals; ``has_next_step`` considers every task on the deal."""
        ...

    def overdue_tasks(self, visibility: Visibility, today: date) -> list[TaskSnapshot]:
        """Open tasks due before ``today``."""
        ...


class SettingsStore(Protocol):
    def load(self) -> Thresholds: ...

    def save(self, thresholds: Thresholds, *, updated_by: UUID) -> None: ...
