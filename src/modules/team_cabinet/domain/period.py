"""Period figures for the manager card and the team reports. Pure Python: the reader
fills them from SQL aggregates; ratios are derived here, never stored."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal


@dataclass(slots=True)
class PeriodMetrics:
    meetings_booked: int = 0  # scheduled in the period, cancellations excluded
    meetings_held: int = 0
    meetings_no_show: int = 0
    meetings_confirmed: int = 0
    meetings_cancelled: int = 0
    calls: int = 0
    stage_changes: int = 0
    payments_count: int = 0
    received: Decimal = Decimal(0)  # payments in the period, refunds not subtracted
    prepayments: int = 0
    sales: int = 0  # deals whose first payment fell in the period (payments.sales)
    tasks_done: int = 0
    # company-local day -> {"calls"|"meetings"|"stage_changes"|"payments": n}
    daily: dict[date, dict[str, int]] = field(default_factory=dict)

    @property
    def attendance(self) -> Decimal | None:
        """Held / (held + no-show): of the meetings that had an outcome, how many happened."""
        judged = self.meetings_held + self.meetings_no_show
        return _ratio(self.meetings_held, judged)

    @property
    def meeting_to_sale(self) -> Decimal | None:
        """Sales of the period / meetings held in the period."""
        return _ratio(self.sales, self.meetings_held)

    def active_on(self, day: date) -> bool:
        return any(n > 0 for n in self.daily.get(day, {}).values())


@dataclass(slots=True)
class Discipline:
    meetings_without_outcome: int = 0
    reschedules_without_date: int = 0
    deals_without_next_step: int = 0
    overdue_tasks: int = 0


def _ratio(part: int, whole: int) -> Decimal | None:
    if whole <= 0:
        return None
    return (Decimal(part) / Decimal(whole)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def average(rows: list[PeriodMetrics]) -> dict[str, Decimal]:
    """Department average per manager for the comparable counters."""
    keys = (
        "meetings_booked",
        "meetings_held",
        "meetings_no_show",
        "calls",
        "stage_changes",
        "payments_count",
        "sales",
        "tasks_done",
    )
    if not rows:
        return {key: Decimal(0) for key in keys}
    n = Decimal(len(rows))
    result = {
        key: (sum((Decimal(getattr(r, key)) for r in rows), Decimal(0)) / n).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
        for key in keys
    }
    result["received"] = (sum((r.received for r in rows), Decimal(0)) / n).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return result
