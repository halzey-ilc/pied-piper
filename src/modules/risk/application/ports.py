from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from modules.risk.domain.rules import AuditEvent, OpenDeal, Thresholds, WonDeal

# The audit actions the rules read.
WATCHED_ACTIONS = ("deal.reopened", "lead.disqualified", "deal.lost", "invoice.voided")


class RiskReader(Protocol):
    """Read-only access to the records and audit entries the rules look at."""

    def won_deals(self, since: datetime) -> list[WonDeal]:
        """Deals won since ``since``; ``has_activity`` covers notes, tasks, calls, meetings."""
        ...

    def open_deals(self) -> list[OpenDeal]: ...

    def audit_events(self, since: datetime, actions: tuple[str, ...]) -> list[AuditEvent]: ...


class SettingsStore(Protocol):
    def load(self) -> Thresholds: ...

    def save(self, thresholds: Thresholds, *, updated_by: UUID) -> None: ...
