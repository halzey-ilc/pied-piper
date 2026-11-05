"""Cabinet board domain: the per-manager day row and the traffic-light rule.

Pure Python: no Django, no ORM. The RED conditions are the ones approved in the
spec. YELLOW/GREEN are not finalised by the owner, so YELLOW is produced only when
explicitly enabled; by default a manager is RED (one or more red reasons) or GREEN.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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
    prepayments: int = 0  # of payments_count: prepayments
    received: Decimal = Decimal(0)
    open_tasks: int = 0
    overdue_tasks: int = 0
    reschedules_without_date: int = 0
    meetings_to_confirm: int = 0  # still scheduled, not confirmed, starting later today
    next_to_confirm_at: datetime | None = None
    meetings_cancelled: int = 0
    tasks_due_today: int = 0
    tasks_done_today: int = 0
    # Month figures for the day score (sales plan pro-rated by elapsed days).
    plan_sales: Decimal | None = None
    sales_month: int = 0
    on_absence: bool = False
    # Filled by evaluate_day_score:
    score: DayScore | None = None
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


@dataclass(frozen=True, slots=True)
class ScoreThresholds:
    """Grade bounds of the 0-100 day score (TZ «Кабинет РОПа», «Оценка дня»: a proposal,
    the owner may tune it). 5 from 90, 4 from 75, 3 from 60, otherwise 2."""

    grade5: int = 90
    grade4: int = 75
    grade3: int = 60


@dataclass(frozen=True, slots=True)
class DayScore:
    """0-100 = result (up to 50) + CRM discipline (up to 50).

    result: sales this month vs the plan pro-rated by elapsed days, capped at 50;
    None when the manager has no plan — then the score is the discipline part scaled
    to 100 and ``plan_missing`` is set.
    discipline: four equal parts (12.5 each) — today's past meetings have an outcome,
    today's meetings are confirmed, reschedules have a new date, no overdue tasks.
    """

    points: int
    grade: int
    result: Decimal | None
    discipline: Decimal
    plan_missing: bool


def _ratio(bad: int, total: int) -> Decimal:
    """Share of the good part (1 when there is nothing to judge)."""
    if total <= 0:
        return Decimal(1)
    return max(Decimal(0), Decimal(total - bad) / Decimal(total))


def evaluate_day_score(
    day: ManagerDay,
    *,
    days_elapsed: int,
    days_total: int,
    thresholds: ScoreThresholds,
) -> DayScore:
    quarter = Decimal("12.5")
    discipline = quarter * (
        _ratio(day.meetings_overdue_outcome, day.meetings_total)
        + (
            Decimal(day.meetings_confirmed) / Decimal(day.meetings_total)
            if day.meetings_total
            else Decimal(1)
        )
        + _ratio(day.reschedules_without_date, day.meetings_total + day.reschedules_without_date)
        + _ratio(day.overdue_tasks, day.open_tasks)
    )
    result: Decimal | None = None
    if day.plan_sales is not None and day.plan_sales > 0 and days_total > 0:
        expected = day.plan_sales * Decimal(max(days_elapsed, 1)) / Decimal(days_total)
        result = min(Decimal(50), Decimal(50) * Decimal(day.sales_month) / expected)
    total = discipline * 2 if result is None else discipline + result
    points = int(total.quantize(Decimal(1)))
    grade = (
        5
        if points >= thresholds.grade5
        else 4
        if points >= thresholds.grade4
        else 3
        if points >= thresholds.grade3
        else 2
    )
    return DayScore(
        points=points,
        grade=grade,
        result=None if result is None else result.quantize(Decimal("0.1")),
        discipline=discipline.quantize(Decimal("0.1")),
        plan_missing=result is None,
    )


@dataclass(slots=True)
class DepartmentTotals:
    managers: int = 0
    calls: int = 0
    meetings_total: int = 0
    meetings_held: int = 0
    payments_count: int = 0
    prepayments: int = 0  # of payments_count: prepayments
    received: Decimal = Decimal(0)
    meetings_confirmed: int = 0
    meetings_to_confirm: int = 0
    meetings_no_show: int = 0
    meetings_cancelled: int = 0
    meetings_overdue_outcome: int = 0
    reschedules_without_date: int = 0
    open_tasks: int = 0
    overdue_tasks: int = 0
    tasks_due_today: int = 0
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
            totals.prepayments += d.prepayments
            totals.received += d.received
            totals.meetings_confirmed += d.meetings_confirmed
            totals.meetings_to_confirm += d.meetings_to_confirm
            totals.meetings_no_show += d.meetings_no_show
            totals.meetings_cancelled += d.meetings_cancelled
            totals.meetings_overdue_outcome += d.meetings_overdue_outcome
            totals.reschedules_without_date += d.reschedules_without_date
            totals.open_tasks += d.open_tasks
            totals.overdue_tasks += d.overdue_tasks
            totals.tasks_due_today += d.tasks_due_today
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
    prepayments: int = 0  # of payments_count: prepayments
    received: Decimal = Decimal(0)
    open_tasks: int = 0
    overdue_tasks: int = 0
    reschedules_without_date: int = field(default=0)
    meetings_to_confirm: int = 0
    next_to_confirm_at: datetime | None = None
    meetings_cancelled: int = 0
    tasks_due_today: int = 0
    tasks_done_today: int = 0
