"""The KPI catalog: the metrics we compute and how.

Metrics are produced from domain events by the read-model handlers (see
infrastructure/handlers.py). A KPI is a named aggregation over one metric, or a
ratio of two.
"""

from __future__ import annotations

from collections.abc import Sequence

from modules.sales_kpi.domain.kpi import Aggregation, KpiDefinition

# Metric names recorded by the event handlers.
METRIC_DEAL_CREATED = "deal_created"
METRIC_DEAL_WON = "deal_won"
METRIC_DEAL_LOST = "deal_lost"
METRIC_DEAL_CLOSED = "deal_closed"
METRIC_REVENUE_WON = "revenue_won"
METRIC_LEAD_CAPTURED = "lead_captured"
METRIC_LEAD_CONVERTED = "lead_converted"
METRIC_ACTIVITY_LOGGED = "activity_logged"
METRIC_TASK_COMPLETED = "task_completed"

_DEFINITIONS: tuple[KpiDefinition, ...] = (
    KpiDefinition("deals_created", "Deals created", Aggregation.COUNT, METRIC_DEAL_CREATED),
    KpiDefinition("deals_won", "Deals won", Aggregation.COUNT, METRIC_DEAL_WON),
    KpiDefinition("revenue_won", "Revenue won", Aggregation.SUM, METRIC_REVENUE_WON, unit="money"),
    KpiDefinition(
        "win_rate",
        "Win rate",
        Aggregation.RATIO,
        METRIC_DEAL_WON,
        denominator_metric=METRIC_DEAL_CLOSED,
        unit="ratio",
    ),
    KpiDefinition("leads_captured", "Leads captured", Aggregation.COUNT, METRIC_LEAD_CAPTURED),
    KpiDefinition(
        "lead_conversion_rate",
        "Lead conversion rate",
        Aggregation.RATIO,
        METRIC_LEAD_CONVERTED,
        denominator_metric=METRIC_LEAD_CAPTURED,
        unit="ratio",
    ),
    KpiDefinition(
        "activities_logged", "Activities logged", Aggregation.COUNT, METRIC_ACTIVITY_LOGGED
    ),
    KpiDefinition("tasks_completed", "Tasks completed", Aggregation.COUNT, METRIC_TASK_COMPLETED),
)


class StaticKpiCatalog:
    def definitions(self) -> Sequence[KpiDefinition]:
        return _DEFINITIONS

    def get(self, code: str) -> KpiDefinition | None:
        return next((d for d in _DEFINITIONS if d.code == code), None)
