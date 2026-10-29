from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol
from uuid import UUID

from modules.access_control.domain.rbac import Principal
from modules.ai_insights.domain.kpi_payload import KpiReading
from modules.ai_insights.domain.report import AiReport, ReportKind, ReportTrigger


@dataclass(frozen=True, slots=True)
class FindingsSnapshot:
    """What the operations rules found for a principal, as digest input parts."""

    thresholds: dict[str, int]
    counts: dict[str, int]
    findings: list[dict[str, Any]]  # {"ref", "kind", "age_days"}, oldest first


class FindingsSource(Protocol):
    def snapshot_for(self, principal: Principal) -> FindingsSnapshot: ...


class KpiSource(Protocol):
    def readings(self, principal: Principal, period: str, start: date) -> list[KpiReading]:
        """Company KPIs for the period; raises AccessDeniedError without reports.view."""
        ...


@dataclass(frozen=True, slots=True)
class RiskSnapshot:
    """The company's current risk signals, as review input parts."""

    window_days: int
    counts: dict[str, int]
    signals: list[dict[str, Any]]  # {"ref", "kind", "value", "threshold"}, newest first


class RiskSource(Protocol):
    def snapshot_for(self, principal: Principal) -> RiskSnapshot:
        """Raises AccessDeniedError without risk.view."""
        ...


@dataclass(frozen=True, slots=True)
class Member:
    user_id: UUID
    role: str


class MemberDirectory(Protocol):
    def active_members(self, tenant_id: UUID) -> list[Member]: ...


class ReportRepository(Protocol):
    def add(self, report: AiReport) -> None: ...

    def get(self, report_id: UUID) -> AiReport | None: ...

    def latest_for_user(self, user_id: UUID, kind: ReportKind) -> AiReport | None: ...

    def find_reusable(self, user_id: UUID, kind: ReportKind, payload_hash: str) -> AiReport | None:
        """An unfinished or succeeded report of the same snapshot, if any."""
        ...

    def exists_for_day(
        self, user_id: UUID, kind: ReportKind, trigger: ReportTrigger, day: date
    ) -> bool: ...

    def save(self, report: AiReport, expected_version: int) -> None: ...


class ReportScheduler(Protocol):
    def schedule_submit(self, *, tenant_slug: str, report_id: UUID) -> None: ...

    def schedule_poll(self, *, tenant_slug: str, report_id: UUID, delay_seconds: float) -> None: ...
