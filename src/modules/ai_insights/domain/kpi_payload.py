"""Deterministic preparation of KPI numbers for the AI (principle 2, ADR-005).

Everything the model may say about numbers is computed here: the current and
previous value, the change and the relative change. Ratios become percentages
and their change is in percentage points. The model only explains.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

PERIODS = ("day", "week", "month")


@dataclass(frozen=True, slots=True)
class KpiReading:
    code: str
    name: str
    unit: str  # catalog unit: "" (count), "money", "ratio"
    value: Decimal | None


def period_start(period: str, day: date) -> date:
    """The first day of the period containing ``day`` (weeks start on Monday)."""
    if period == "day":
        return day
    if period == "week":
        return day - timedelta(days=day.weekday())
    if period == "month":
        return day.replace(day=1)
    raise ValueError(f"unknown period {period!r}")


def next_start(period: str, start: date) -> date:
    if period == "day":
        return start + timedelta(days=1)
    if period == "week":
        return start + timedelta(days=7)
    return date(start.year + start.month // 12, start.month % 12 + 1, 1)


def previous_start(period: str, start: date) -> date:
    if period == "day":
        return start - timedelta(days=1)
    if period == "week":
        return start - timedelta(days=7)
    return (start - timedelta(days=1)).replace(day=1)


def _unit(catalog_unit: str) -> str:
    return {"money": "money", "ratio": "percent"}.get(catalog_unit, "count")


def _number(value: Decimal | None, unit: str) -> Decimal | None:
    if value is None:
        return None
    if unit == "percent":
        value = value * 100
    places = {"count": Decimal(1), "money": Decimal("0.01"), "percent": Decimal("0.1")}[unit]
    return value.quantize(places, rounding=ROUND_HALF_UP)


def _json(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else float(value)


def build_payload(
    *,
    language: str,
    period: str,
    start: date,
    current: list[KpiReading],
    previous: list[KpiReading],
) -> dict[str, Any]:
    """The kpi_explanation.input.v1 document for a period and the one before it."""
    prev_start = previous_start(period, start)
    prev_by_code = {r.code: r for r in previous}
    metrics = []
    for reading in current:
        unit = _unit(reading.unit)
        cur = _number(reading.value, unit)
        prev_reading = prev_by_code.get(reading.code)
        prev = _number(prev_reading.value, unit) if prev_reading else None
        change = (cur - prev) if cur is not None and prev is not None else None
        change_pct = (
            ((cur - prev) / prev * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            if cur is not None and prev is not None and prev != 0
            else None
        )
        metrics.append(
            {
                "code": reading.code,
                "name": reading.name,
                "unit": unit,
                "current": _json(cur),
                "previous": _json(prev),
                "change": _json(change),
                "change_pct": _json(change_pct),
            }
        )
    return {
        "language": language,
        "period": {
            "type": period,
            "start": start.isoformat(),
            "end": next_start(period, start).isoformat(),
        },
        "previous_period": {
            "type": period,
            "start": prev_start.isoformat(),
            "end": start.isoformat(),
        },
        "metrics": metrics,
    }
