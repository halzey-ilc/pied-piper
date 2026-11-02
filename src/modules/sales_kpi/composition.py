"""Wires the KPI read model and analytics service to the active tenant database."""

from __future__ import annotations

from modules.sales_kpi.application.calculator import KpiCalculator, KpiSnapshotWriter
from modules.sales_kpi.application.catalog import StaticKpiCatalog
from modules.sales_kpi.application.crm_counts import CrmCountsService
from modules.sales_kpi.application.service import AnalyticsService
from modules.sales_kpi.infrastructure.crm_counts import DjangoCrmCounter
from modules.sales_kpi.infrastructure.read_model import (
    DjangoKpiSnapshotStore,
    DjangoMetricAggregator,
)
from platform_core.application.context import require_tenant


def analytics_service() -> AnalyticsService:
    alias = require_tenant().db_alias
    return AnalyticsService(
        calculator=KpiCalculator(StaticKpiCatalog(), DjangoMetricAggregator(alias)),
        snapshots=DjangoKpiSnapshotStore(alias),
    )


def snapshot_writer(alias: str) -> KpiSnapshotWriter:
    return KpiSnapshotWriter(
        StaticKpiCatalog(), DjangoMetricAggregator(alias), DjangoKpiSnapshotStore(alias)
    )


def crm_counts_service() -> CrmCountsService:
    return CrmCountsService(DjangoCrmCounter(require_tenant().db_alias))
