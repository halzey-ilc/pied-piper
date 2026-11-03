"""Pure rules of the automatic reports: when they are due and who gets what."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from modules.notifications.domain.daily_reports import (
    MAX_BODY,
    EveningFigures,
    MessageKind,
    MorningFigures,
    ReportInput,
    ReportKind,
    ReportSchedule,
    StaffMember,
    evening_messages,
    morning_messages,
)

ALMATY = ZoneInfo("Asia/Almaty")
DAY = date(2026, 11, 3)


def _local(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 3, hour, minute, tzinfo=ALMATY)


def test_due_from_local_time_within_the_catch_up_window() -> None:
    schedule = ReportSchedule(morning_time=time(9, 0), evening_time=time(19, 0))
    assert not schedule.is_due(ReportKind.MORNING, _local(8, 59))
    assert schedule.is_due(ReportKind.MORNING, _local(9, 0))
    assert schedule.is_due(ReportKind.MORNING, _local(11, 59))
    assert not schedule.is_due(ReportKind.MORNING, _local(12, 0))  # too late for "morning"
    assert schedule.is_due(ReportKind.EVENING, _local(19, 5))
    assert not ReportSchedule(evening_enabled=False).is_due(ReportKind.EVENING, _local(19, 5))


def _member(name: str, role: str, department: UUID | None = None) -> StaffMember:
    return StaffMember(uuid4(), name, role, department, "North" if department else "")


def test_department_without_head_alerts_the_director() -> None:
    north, south = uuid4(), uuid4()
    head = _member("Head", "head_of_sales", north)
    director = _member("Boss", "director")
    a = _member("Aida", "manager", north)
    b = _member("Bek", "manager", south)
    c = _member("Cholpon", "manager", north)
    data = ReportInput(
        day=DAY,
        currency="KZT",
        staff=[head, director, a, b, c],
        absent=set(),
        evening={a.user_id: EveningFigures(payments_confirmed=1, confirmed_amount=Decimal(5000))},
    )
    messages = evening_messages(data)
    alerts = {m.recipient_user_id: m for m in messages if m.kind is MessageKind.INACTIVITY}
    assert set(alerts) == {head.user_id, director.user_id}
    assert alerts[head.user_id].body.startswith("Cholpon —")
    assert alerts[director.user_id].body.startswith("Bek —")  # south has no head of sales
    assert alerts[head.user_id].title.endswith("1 менеджер")
    keys = [m.key for m in messages]
    assert len(keys) == len(set(keys))
    [mine] = [m for m in messages if m.recipient_user_id == a.user_id]
    assert "подтверждено 1 на 5 000 KZT" in mine.body


def test_long_department_summaries_are_clipped() -> None:
    north = uuid4()
    head = _member("Head", "head_of_sales", north)
    managers = [_member(f"Manager number {i:03d}", "manager", north) for i in range(60)]
    data = ReportInput(
        day=DAY,
        currency="KGS",
        staff=[head, *managers],
        absent=set(),
        morning={m.user_id: MorningFigures(tasks_due_today=1) for m in managers},
    )
    [summary] = [m for m in morning_messages(data) if m.recipient_user_id == head.user_id]
    assert len(summary.body) <= MAX_BODY and summary.body.endswith("…")
    assert "Итого по отделу: задачи на сегодня 60" in summary.body
