"""Django adapters for the KPI read model: recorder, aggregator, snapshot store."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.db.models import Count, Max, Sum

from modules.sales_kpi.domain.kpi import Aggregation, KpiSnapshot, Period
from modules.sales_kpi.infrastructure.models import KpiSnapshotRecord, MetricFact


class DjangoMetricRecorder:
    def __init__(self, using: str) -> None:
        self.using = using

    def record(
        self,
        *,
        source_event_id: UUID,
        metric: str,
        value: Decimal,
        occurred_at: datetime,
        owner_user_id: UUID | None,
    ) -> None:
        # ignore_conflicts makes a repeated event a no-op (unique event+metric).
        MetricFact.objects.using(self.using).bulk_create(
            [
                MetricFact(
                    source_event_id=source_event_id,
                    metric=metric,
                    value=value,
                    occurred_at=occurred_at,
                    owner_user_id=owner_user_id,
                )
            ],
            ignore_conflicts=True,
        )


class DjangoMetricAggregator:
    def __init__(self, using: str) -> None:
        self.using = using

    def _sum(self, metric: str, start: datetime, end: datetime) -> Decimal:
        total = (
            MetricFact.objects.using(self.using)
            .filter(metric=metric, occurred_at__gte=start, occurred_at__lt=end)
            .aggregate(total=Sum("value"))["total"]
        )
        return total if total is not None else Decimal(0)

    def aggregate(
        self,
        *,
        metric: str,
        aggregation: Aggregation,
        start: datetime,
        end: datetime,
        denominator_metric: str | None = None,
    ) -> Decimal | None:
        window = MetricFact.objects.using(self.using).filter(
            metric=metric, occurred_at__gte=start, occurred_at__lt=end
        )
        match aggregation:
            case Aggregation.COUNT:
                return Decimal(window.aggregate(n=Count("id"))["n"] or 0)
            case Aggregation.SUM:
                return self._sum(metric, start, end)
            case Aggregation.AVG:
                result = window.aggregate(avg=Sum("value"), n=Count("id"))
                count = result["n"] or 0
                return (result["avg"] / count) if count else None
            case Aggregation.RATIO:
                if denominator_metric is None:
                    return None
                denominator = self._sum(denominator_metric, start, end)
                return (self._sum(metric, start, end) / denominator) if denominator else None

    def watermark(self, metrics: Sequence[str], start: datetime, end: datetime) -> datetime | None:
        result: datetime | None = (
            MetricFact.objects.using(self.using)
            .filter(metric__in=list(metrics), occurred_at__gte=start, occurred_at__lt=end)
            .aggregate(latest=Max("occurred_at"))["latest"]
        )
        return result


class DjangoKpiSnapshotStore:
    def __init__(self, using: str) -> None:
        self.using = using

    def upsert(self, snapshot: KpiSnapshot) -> None:
        KpiSnapshotRecord.objects.using(self.using).update_or_create(
            kpi_code=snapshot.kpi_code,
            period=snapshot.period.value,
            period_start=snapshot.period_start,
            defaults={
                "value": snapshot.value,
                "computed_at": snapshot.computed_at,
                "source_watermark": snapshot.source_watermark,
            },
        )

    def list_between(self, period: Period, start: date, end: date) -> list[KpiSnapshot]:
        rows = (
            KpiSnapshotRecord.objects.using(self.using)
            .filter(period=period.value, period_start__gte=start, period_start__lte=end)
            .order_by("period_start", "kpi_code")
        )
        return [
            KpiSnapshot(
                snapshot_id=row.id,
                kpi_code=row.kpi_code,
                period=Period(row.period),
                period_start=row.period_start,
                value=row.value,
                dimensions={},
                computed_at=row.computed_at,
                source_watermark=row.source_watermark,
            )
            for row in rows
        ]
