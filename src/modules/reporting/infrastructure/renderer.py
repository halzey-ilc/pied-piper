"""Renders reports from the KPI read model. Runs in the worker, not in a request."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta

from modules.reporting.domain.report import ReportRequest, ReportType
from modules.sales_kpi.application.catalog import StaticKpiCatalog
from modules.sales_kpi.infrastructure.read_model import DjangoMetricAggregator
from shared.domain.errors import DomainValidationError


class KpiCsvRenderer:
    def __init__(self, using: str) -> None:
        self._aggregator = DjangoMetricAggregator(using)
        self._catalog = StaticKpiCatalog()

    def render(self, request: ReportRequest) -> tuple[bytes, str]:
        if request.report_type is not ReportType.KPI_SUMMARY:
            raise DomainValidationError(f"Unsupported report type: {request.report_type.value}.")
        start = datetime(
            request.period_start.year,
            request.period_start.month,
            request.period_start.day,
            tzinfo=UTC,
        )
        # Inclusive end date -> half-open end at the start of the next day.
        end = datetime(
            request.period_end.year, request.period_end.month, request.period_end.day, tzinfo=UTC
        ) + timedelta(days=1)

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["kpi_code", "kpi_name", "aggregation", "unit", "value"])
        for definition in self._catalog.definitions():
            value = self._aggregator.aggregate(
                metric=definition.metric,
                aggregation=definition.aggregation,
                start=start,
                end=end,
                denominator_metric=definition.denominator_metric,
            )
            writer.writerow(
                [
                    definition.code,
                    definition.name,
                    definition.aggregation.value,
                    definition.unit,
                    "" if value is None else str(value),
                ]
            )
        return buffer.getvalue().encode("utf-8"), "text/csv"
