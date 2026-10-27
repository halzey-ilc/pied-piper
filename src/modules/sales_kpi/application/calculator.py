"""Computes KPI values from the metric read model, for the API (current values)
and for stored snapshots (history). Aggregation runs in the database, so this
never loads raw facts into memory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from modules.sales_kpi.application.ports import KpiCatalog, KpiSnapshotStore, MetricAggregator
from modules.sales_kpi.domain.kpi import KpiDefinition, KpiSnapshot, Period


def period_bounds(period: Period, period_start: date) -> tuple[datetime, datetime]:
    """Half-open [start, end) in UTC for the period beginning on ``period_start``."""
    start = datetime(period_start.year, period_start.month, period_start.day, tzinfo=UTC)
    if period is Period.DAY:
        return start, start + timedelta(days=1)
    if period is Period.WEEK:
        return start, start + timedelta(days=7)
    # MONTH: to the first day of the next month.
    if period_start.month == 12:
        end_date = date(period_start.year + 1, 1, 1)
    else:
        end_date = date(period_start.year, period_start.month + 1, 1)
    return start, datetime(end_date.year, end_date.month, end_date.day, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class KpiValue:
    code: str
    name: str
    aggregation: str
    unit: str
    value: Decimal | None
    target: Decimal | None


class KpiCalculator:
    def __init__(self, catalog: KpiCatalog, aggregator: MetricAggregator) -> None:
        self._catalog = catalog
        self._aggregator = aggregator

    def _value(self, definition: KpiDefinition, start: datetime, end: datetime) -> Decimal | None:
        return self._aggregator.aggregate(
            metric=definition.metric,
            aggregation=definition.aggregation,
            start=start,
            end=end,
            denominator_metric=definition.denominator_metric,
        )

    def compute(self, period: Period, period_start: date) -> list[KpiValue]:
        start, end = period_bounds(period, period_start)
        return [
            KpiValue(
                code=d.code,
                name=d.name,
                aggregation=d.aggregation.value,
                unit=d.unit,
                value=self._value(d, start, end),
                target=d.target,
            )
            for d in self._catalog.definitions()
        ]


class KpiSnapshotWriter:
    def __init__(
        self, catalog: KpiCatalog, aggregator: MetricAggregator, store: KpiSnapshotStore
    ) -> None:
        self._calc = KpiCalculator(catalog, aggregator)
        self._catalog = catalog
        self._aggregator = aggregator
        self._store = store

    def compute_and_store(self, period: Period, period_start: date) -> list[KpiSnapshot]:
        start, end = period_bounds(period, period_start)
        metrics = [d.metric for d in self._catalog.definitions()]
        watermark = self._aggregator.watermark(metrics, start, end)
        now = datetime.now(UTC)
        snapshots: list[KpiSnapshot] = []
        for value in self._calc.compute(period, period_start):
            snapshot = KpiSnapshot(
                snapshot_id=uuid4(),
                kpi_code=value.code,
                period=period,
                period_start=period_start,
                value=value.value,
                dimensions={},
                computed_at=now,
                source_watermark=watermark,
            )
            self._store.upsert(snapshot)
            snapshots.append(snapshot)
        return snapshots
