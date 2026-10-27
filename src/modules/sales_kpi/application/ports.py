"""Ports for KPI calculation. Adapters (read models, snapshot storage) are not implemented yet."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Protocol

from modules.sales_kpi.domain.kpi import KpiDefinition, KpiSnapshot, MetricSample, Period


class MetricReadModel(Protocol):
    """Denormalised facts maintained from domain events (not from API request paths)."""

    def samples(
        self, metrics: Sequence[str], period: Period, period_start: date
    ) -> Sequence[MetricSample]: ...


class KpiSnapshotStore(Protocol):
    def upsert(self, snapshot: KpiSnapshot) -> None:
        """Idempotent on (kpi_code, period, period_start, dimensions)."""
        ...

    def get(
        self, kpi_code: str, period: Period, period_start: date, dimensions: Mapping[str, str]
    ) -> KpiSnapshot | None: ...


class KpiCatalog(Protocol):
    def definitions(self) -> Sequence[KpiDefinition]: ...
