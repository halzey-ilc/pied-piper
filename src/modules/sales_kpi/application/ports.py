"""Ports for the KPI read model and snapshots."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from modules.sales_kpi.domain.kpi import Aggregation, KpiDefinition, KpiSnapshot, Period


class MetricRecorder(Protocol):
    """Writes denormalised metric facts, idempotently on (source_event_id, metric)."""

    def record(
        self,
        *,
        source_event_id: UUID,
        metric: str,
        value: Decimal,
        occurred_at: datetime,
        owner_user_id: UUID | None,
    ) -> None: ...


class MetricAggregator(Protocol):
    """Aggregates metric facts in the database over a half-open time window."""

    def aggregate(
        self,
        *,
        metric: str,
        aggregation: Aggregation,
        start: datetime,
        end: datetime,
        denominator_metric: str | None = None,
    ) -> Decimal | None: ...

    def watermark(self, metrics: Sequence[str], start: datetime, end: datetime) -> datetime | None:
        """Newest fact timestamp in the window (for incremental snapshots)."""
        ...


class KpiSnapshotStore(Protocol):
    def upsert(self, snapshot: KpiSnapshot) -> None:
        """Idempotent on (kpi_code, period, period_start)."""
        ...

    def list_between(self, period: Period, start: date, end: date) -> list[KpiSnapshot]: ...


class KpiCatalog(Protocol):
    def definitions(self) -> Sequence[KpiDefinition]: ...

    def get(self, code: str) -> KpiDefinition | None: ...
