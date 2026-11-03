"""Task deadline alert rules: reminders, overdue alerts, escalations and their keys."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from modules.notifications.domain.deadlines import AlertKind, TaskDeadline, deadline_alerts

TODAY = date(2026, 11, 10)
REP, HEAD = uuid4(), uuid4()


def _task(due: date, assignee=REP) -> TaskDeadline:  # type: ignore[no-untyped-def]
    return TaskDeadline(uuid4(), "Позвонить", assignee, due, "deal", uuid4())


def _alerts(*tasks: TaskDeadline, days_before: int = 1, head=HEAD):  # type: ignore[no-untyped-def]
    return deadline_alerts(
        tasks, TODAY, reminder_days_for=lambda _: days_before, head_of=lambda _: head
    )


def test_reminder_within_the_lead_time_only() -> None:
    assert [a.kind for a in _alerts(_task(date(2026, 11, 11)))] == [AlertKind.REMINDER]
    assert _alerts(_task(date(2026, 11, 12))) == []  # two days ahead, lead time 1 day
    assert [a.days for a in _alerts(_task(date(2026, 11, 12)), days_before=3)] == [2]
    assert [a.kind for a in _alerts(_task(TODAY), days_before=0)] == [AlertKind.REMINDER]


def test_overdue_goes_to_the_assignee_and_escalates_after_the_threshold() -> None:
    one_day = _alerts(_task(date(2026, 11, 9)))
    assert [(a.kind, a.recipient_user_id) for a in one_day] == [(AlertKind.OVERDUE, REP)]
    two_days = _alerts(_task(date(2026, 11, 8)))
    assert [(a.kind, a.recipient_user_id, a.days) for a in two_days] == [
        (AlertKind.OVERDUE, REP, 2),
        (AlertKind.ESCALATION, HEAD, 2),
    ]


def test_no_escalation_without_a_head_or_to_oneself() -> None:
    assert [a.kind for a in _alerts(_task(date(2026, 11, 1)), head=None)] == [AlertKind.OVERDUE]
    own = _alerts(_task(date(2026, 11, 1), assignee=HEAD))
    assert [a.kind for a in own] == [AlertKind.OVERDUE]


def test_keys_are_stable_and_restart_when_the_due_date_moves() -> None:
    task = _task(date(2026, 11, 9))
    assert _alerts(task)[0].key == _alerts(task)[0].key
    moved = TaskDeadline(task.task_id, task.title, REP, date(2026, 11, 8), "deal", task.subject_id)
    assert _alerts(moved)[0].key != _alerts(task)[0].key
