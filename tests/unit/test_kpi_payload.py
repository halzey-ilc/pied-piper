"""KPI explanation input: every number is computed here, deterministically."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from modules.ai_insights.domain.kpi_payload import (
    KpiReading,
    build_payload,
    next_start,
    period_start,
    previous_start,
)


@pytest.mark.parametrize(
    ("period", "day", "start", "prev", "nxt"),
    [
        ("month", date(2026, 10, 28), date(2026, 10, 1), date(2026, 9, 1), date(2026, 11, 1)),
        ("month", date(2026, 1, 15), date(2026, 1, 1), date(2025, 12, 1), date(2026, 2, 1)),
        ("month", date(2026, 12, 3), date(2026, 12, 1), date(2026, 11, 1), date(2027, 1, 1)),
        ("week", date(2026, 10, 29), date(2026, 10, 26), date(2026, 10, 19), date(2026, 11, 2)),
        ("day", date(2026, 3, 1), date(2026, 3, 1), date(2026, 2, 28), date(2026, 3, 2)),
    ],
)
def test_period_arithmetic(period: str, day: date, start: date, prev: date, nxt: date) -> None:
    assert period_start(period, day) == start
    assert previous_start(period, start) == prev
    assert next_start(period, start) == nxt


def test_changes_are_computed_by_the_crm() -> None:
    current = [
        KpiReading("revenue_won", "Revenue won", "money", Decimal("1250000.00")),
        KpiReading("win_rate", "Win rate", "ratio", Decimal("0.3247")),
        KpiReading("deals_won", "Deals won", "", Decimal("12")),
        KpiReading("leads_captured", "Leads captured", "", Decimal("0")),
    ]
    previous = [
        KpiReading("revenue_won", "Revenue won", "money", Decimal("1000000.00")),
        KpiReading("win_rate", "Win rate", "ratio", Decimal("0.4")),
        KpiReading("deals_won", "Deals won", "", None),
        KpiReading("leads_captured", "Leads captured", "", Decimal("0")),
    ]
    payload = build_payload(
        language="ru", period="month", start=date(2026, 10, 1), current=current, previous=previous
    )
    assert payload["period"] == {"type": "month", "start": "2026-10-01", "end": "2026-11-01"}
    assert payload["previous_period"] == {
        "type": "month",
        "start": "2026-09-01",
        "end": "2026-10-01",
    }
    by_code = {m["code"]: m for m in payload["metrics"]}
    assert by_code["revenue_won"] == {
        "code": "revenue_won", "name": "Revenue won", "unit": "money",
        "current": 1250000, "previous": 1000000, "change": 250000, "change_pct": 25,
    }  # fmt: skip
    # Ratios become percentages; their change is in percentage points.
    assert by_code["win_rate"]["unit"] == "percent"
    assert by_code["win_rate"]["current"] == 32.5
    assert by_code["win_rate"]["change"] == -7.5
    assert by_code["win_rate"]["change_pct"] == -18.8
    # Unknown previous value: no change is claimed.
    assert by_code["deals_won"]["change"] is None and by_code["deals_won"]["change_pct"] is None
    # Zero previous value: no division by zero, no relative change.
    assert by_code["leads_captured"]["change"] == 0
    assert by_code["leads_captured"]["change_pct"] is None
