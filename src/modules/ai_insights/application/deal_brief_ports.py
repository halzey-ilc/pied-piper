from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID

from modules.access_control.domain.rbac import Principal
from modules.ai_insights.domain.deal_brief import DealBrief, DealFacts, HistoryEntry


@dataclass(frozen=True, slots=True)
class DealContext:
    """A deal and its history, exactly as the principal can see them."""

    facts: DealFacts
    owner_user_id: UUID
    team_id: UUID | None
    history: list[HistoryEntry]


class DealContextReader(Protocol):
    def read(self, principal: Principal, deal_id: UUID, today: date) -> DealContext:
        """Raises NotFoundError if the principal cannot see the deal. History the
        principal may not view (e.g. calls without telephony.view) is left out."""
        ...


class DealBriefRepository(Protocol):
    def add(self, brief: DealBrief) -> None: ...

    def get(self, brief_id: UUID) -> DealBrief | None: ...

    def latest_for(self, deal_id: UUID, user_id: UUID) -> DealBrief | None: ...

    def find_reusable(self, deal_id: UUID, user_id: UUID, source_hash: str) -> DealBrief | None:
        """The user's unfinished or succeeded brief for the same input, if any."""
        ...

    def save(self, brief: DealBrief, expected_version: int) -> None: ...


class DealBriefScheduler(Protocol):
    def schedule_submit(self, *, tenant_slug: str, brief_id: UUID) -> None: ...

    def schedule_poll(self, *, tenant_slug: str, brief_id: UUID, delay_seconds: float) -> None: ...
