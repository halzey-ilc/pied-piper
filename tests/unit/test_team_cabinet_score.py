"""Day score (TZ «Кабинет РОПа», «Оценка дня»): result vs plan + CRM discipline."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from modules.team_cabinet.domain.board import ManagerDay, ScoreThresholds, evaluate_day_score


def _day(**kw: object) -> ManagerDay:
    day = ManagerDay(user_id=uuid4(), username="m", department_id=None, product_role="sales_rep")
    for key, value in kw.items():
        setattr(day, key, value)
    return day


def test_full_discipline_and_plan_on_pace_is_grade_5() -> None:
    day = _day(
        meetings_total=4, meetings_confirmed=4, open_tasks=2, plan_sales=Decimal(30), sales_month=10
    )
    score = evaluate_day_score(day, days_elapsed=10, days_total=30, thresholds=ScoreThresholds())
    assert score.points == 100 and score.grade == 5 and not score.plan_missing


def test_without_plan_discipline_is_scaled_to_100() -> None:
    day = _day(meetings_total=2, meetings_confirmed=1, open_tasks=4, overdue_tasks=2)
    score = evaluate_day_score(day, days_elapsed=5, days_total=30, thresholds=ScoreThresholds())
    # 12.5 + 6.25 + 12.5 + 6.25 = 37.5 -> doubled = 75
    assert score.plan_missing and score.points == 75 and score.grade == 4


def test_behind_plan_and_tails_drop_the_grade() -> None:
    day = _day(
        meetings_total=4,
        meetings_overdue_outcome=2,
        meetings_confirmed=1,
        open_tasks=4,
        overdue_tasks=4,
        plan_sales=Decimal(30),
        sales_month=2,
    )
    score = evaluate_day_score(day, days_elapsed=15, days_total=30, thresholds=ScoreThresholds())
    assert score.grade == 2
