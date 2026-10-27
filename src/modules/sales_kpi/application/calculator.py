"""Computes KPI snapshots from a read model. Intended to run in the ``reports``
Celery queue (daily schedule), never inside an API request."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from uuid import uuid4

from modules.sales_kpi.application.ports import KpiCatalog, KpiSnapshotStore, MetricReadModel
from modules.sales_kpi.domain.kpi import KpiSnapshot, Period, aggregate


class KpiCalculator:
    def __init__(
        self,
        catalog: KpiCatalog,
        read_model: MetricReadModel,
        store: KpiSnapshotStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._catalog = catalog
        self._read_model = read_model
        self._store = store
        self._clock = clock

    def compute_period(self, period: Period, period_start: date) -> list[KpiSnapshot]:
        snapshots: list[KpiSnapshot] = []
        for definition in self._catalog.definitions():
            metrics = [definition.metric]
            if definition.denominator_metric:
                metrics.append(definition.denominator_metric)
            samples = self._read_model.samples(metrics, period, period_start)
            snapshot = KpiSnapshot(
                snapshot_id=uuid4(),
                kpi_code=definition.code,
                period=period,
                period_start=period_start,
                value=aggregate(definition, samples),
                dimensions={},
                computed_at=self._clock(),
                source_watermark=max((s.occurred_at for s in samples), default=None),
            )
            self._store.upsert(snapshot)
            snapshots.append(snapshot)
        return snapshots
