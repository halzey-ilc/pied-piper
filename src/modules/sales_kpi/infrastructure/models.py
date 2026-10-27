from __future__ import annotations

import uuid

from django.db import models


class MetricFact(models.Model):
    """A single denormalised metric observation, derived from a domain event.

    The read model for KPIs. (source_event_id, metric) is unique so re-processing
    an event is idempotent even outside the inbox dedupe path.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_event_id = models.UUIDField()
    metric = models.CharField(max_length=64)
    value = models.DecimalField(max_digits=18, decimal_places=2)
    occurred_at = models.DateTimeField()
    owner_user_id = models.UUIDField(null=True, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sales_kpi_metric_fact"
        constraints = [
            models.UniqueConstraint(
                fields=["source_event_id", "metric"], name="kpi_fact_event_metric"
            ),
        ]
        indexes = [
            models.Index(fields=["metric", "occurred_at"], name="kpi_fact_metric_time_idx"),
            models.Index(fields=["owner_user_id"], name="kpi_fact_owner_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.metric}={self.value}"


class KpiSnapshotRecord(models.Model):
    """A computed KPI value for a period; history for dashboards and trends."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kpi_code = models.CharField(max_length=64)
    period = models.CharField(max_length=8)
    period_start = models.DateField()
    value = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    computed_at = models.DateTimeField()
    source_watermark = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "sales_kpi_snapshot"
        constraints = [
            models.UniqueConstraint(
                fields=["kpi_code", "period", "period_start"], name="kpi_snapshot_unique"
            ),
        ]
        indexes = [
            models.Index(fields=["period", "period_start"], name="kpi_snapshot_period_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.kpi_code}@{self.period_start}={self.value}"
