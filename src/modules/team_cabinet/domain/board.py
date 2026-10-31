"""Cabinet board domain: the per-manager day row and the traffic-light rule.

Pure Python: no Django, no ORM. The RED conditions are the ones approved in the
spec. YELLOW/GREEN are not finalised by the owner, so YELLOW is produced only when
explicitly enabled; by default a manager is RED (one or more red reasons) or GREEN.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class TrafficThresholds:
    meeting_outcome_minutes: int = 45
    morning_confirm_deadline_hour: int = 10
    morning_cutoff_hour: int = 12
    morning_confirm_ratio: float = 0.40
    yellow_rules_enabled: bool = False


@dataclass(slots=True)
class ManagerDay:
    user_id: UUID
    username: str
    department_id: UUID | None
    product_role: str
    calls: int = 0
    meetings_total: int = 0
    meetings_confirmed: int = 0
    meetings_held: int = 0
    meetings_no_show: int = 0
    meetings_overdue_outcome: int = 0  # started > N min ago, still without an outcome
    morning_meetings: int = 0
    morning_confirmed: int = 0
    payments_count: int = 0
    received: Decimal = Decimal(0)
    open_tasks: int = 0
    overdue_tasks: int = 0
    reschedules_without_date: int = 0
    on_absence: bool = False
    # Filled by evaluate_traffic_light:
    status: str = "green"
    red_reasons: tuple[str, ...] = ()
    yellow_reasons: tuple[str, ...] = ()

    @property
    def has_activity(self) -> bool:
        # Activity = calls, meetings or payments today (spec's notion of "not silent").
        return self.calls > 0 or self.meetings_total > 0 or self.payments_count > 0


def evaluate_traffic_light(
    day: ManagerDay,
    *,
    now_hour: int,
    thresholds: TrafficThresholds,
    is_working_day: bool,
) -> None:
    """Set day.status / red_reasons / yellow_reasons in place (approved RED rules)."""
    red: list[str] = []
    # No activity on a working day (a planned absence is not silence).
    if is_working_day and not day.has_activity and not day.on_absence:
        red.append("no_activity")
    # A meeting that started over N minutes ago still has no outcome.
    if day.meetings_overdue_outcome > 0:
        red.append("meeting_without_outcome")
    # By the deadline, too few of the morning meetings are confirmed.
    if (
        now_hour >= thresholds.morning_confirm_deadline_hour
        and day.morning_meetings > 0
        and (day.morning_confirmed / day.morning_meetings) < thresholds.morning_confirm_ratio
    ):
        red.append("morning_confirmations_below_threshold")

    yellow: list[str] = []
    if thresholds.yellow_rules_enabled and not red:
        # Proposed (not approved): overdue tasks or date-less reschedules.
        if day.overdue_tasks > 0:
            yellow.append("overdue_tasks")
        if day.reschedules_without_date > 0:
            yellow.append("reschedules_without_date")

    day.red_reasons = tuple(red)
    day.yellow_reasons = tuple(yellow)
    day.status = "red" if red else ("yellow" if yellow else "green")


@dataclass(slots=True)
class DepartmentTotals:
    managers: int = 0
    calls: int = 0
    meetings_total: int = 0
    meetings_held: int = 0
    payments_count: int = 0
    received: Decimal = Decimal(0)
    red: int = 0
    yellow: int = 0
    green: int = 0

    @classmethod
    def of(cls, days: list[ManagerDay]) -> DepartmentTotals:
        totals = cls(managers=len(days))
        for d in days:
            totals.calls += d.calls
            totals.meetings_total += d.meetings_total
            totals.meetings_held += d.meetings_held
            totals.payments_count += d.payments_count
            totals.received += d.received
            totals.red += 1 if d.status == "red" else 0
            totals.yellow += 1 if d.status == "yellow" else 0
            totals.green += 1 if d.status == "green" else 0
        return totals


@dataclass(frozen=True, slots=True)
class Member:
    user_id: UUID
    username: str
    department_id: UUID | None
    product_role: str


@dataclass(slots=True)
class RawDay:
    """Aggregates read from the data stores for one manager, before scoring."""

    calls: int = 0
    meetings_total: int = 0
    meetings_confirmed: int = 0
    meetings_held: int = 0
    meetings_no_show: int = 0
    meetings_overdue_outcome: int = 0
    morning_meetings: int = 0
    morning_confirmed: int = 0
    payments_count: int = 0
    received: Decimal = Decimal(0)
    open_tasks: int = 0
    overdue_tasks: int = 0
    reschedules_without_date: int = field(default=0)
