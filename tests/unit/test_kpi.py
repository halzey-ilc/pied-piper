from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from modules.sales_kpi.application.calculator import KpiCalculator, period_bounds
from modules.sales_kpi.application.catalog import StaticKpiCatalog
from modules.sales_kpi.domain.kpi import Aggregation, Period


def test_period_bounds() -> None:
    assert period_bounds(Period.DAY, date(2026, 3, 10)) == (
        datetime(2026, 3, 10, tzinfo=UTC),
        datetime(2026, 3, 11, tzinfo=UTC),
    )
    assert period_bounds(Period.WEEK, date(2026, 3, 9)) == (
        datetime(2026, 3, 9, tzinfo=UTC),
        datetime(2026, 3, 16, tzinfo=UTC),
    )
    # Month rolls over the year boundary.
    assert period_bounds(Period.MONTH, date(2026, 12, 1)) == (
        datetime(2026, 12, 1, tzinfo=UTC),
        datetime(2027, 1, 1, tzinfo=UTC),
    )


class _FakeAggregator:
    def __init__(self, values: dict[str, Decimal | None]) -> None:
        self.values = values
        self.calls: list[tuple[str, str, str | None]] = []

    def aggregate(self, *, metric, aggregation, start, end, denominator_metric=None):
        self.calls.append((metric, aggregation.value, denominator_metric))
        return self.values.get(metric)

    def watermark(self, metrics, start, end):
        return None


def test_calculator_maps_definitions_to_aggregator() -> None:
    aggregator = _FakeAggregator(
        {"deal_won": Decimal(3), "revenue_won": Decimal("1500.00"), "deal_created": Decimal(10)}
    )
    values = {
        v.code: v
        for v in KpiCalculator(StaticKpiCatalog(), aggregator).compute(
            Period.MONTH, date(2026, 3, 1)
        )
    }
    assert values["deals_won"].value == Decimal(3)
    assert values["revenue_won"].value == Decimal("1500.00")
    assert values["deals_created"].value == Decimal(10)
    # win_rate is a RATIO and asks the aggregator with a denominator metric.
    assert values["win_rate"].aggregation == Aggregation.RATIO.value
    assert ("deal_won", "ratio", "deal_closed") in aggregator.calls
