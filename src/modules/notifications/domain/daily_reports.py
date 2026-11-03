"""Automatic morning and evening reports (in-app) and the inactivity alert.

Pure rules: when a report is due (company-local time of day), what each role receives and
the texts. Delivery is in-app only (no e-mail/Telegram provider is connected).

- morning (default 09:00): a sales manager gets their day (tasks due today, overdue tasks,
  meetings today, open deals without a next step); a head of sales gets the department
  summary per manager (and who has not started the shift, when the company uses work
  sessions); a director gets the company summary per department;
- evening (default 19:00): the day's results (calls, meetings held/no-show, tasks done,
  deals moved/lost, payments recorded and confirmed — only confirmed money counts, new
  leads). Managers with no activity at all are listed explicitly, and their head of sales
  (or the director, when a department has no head) gets a separate inactivity alert.

A report is sent from its time until ``CATCH_UP`` later (a beat that was down for a short
while still delivers; a company created in the afternoon gets no "morning" report). Every
notification carries a stable key (kind, local date, recipient): at most one per day.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid5

CATCH_UP = timedelta(hours=3)
DEFAULT_MORNING = time(9, 0)
DEFAULT_EVENING = time(19, 0)

MAX_BODY = 1000
MAX_TITLE = 200

_KEY_NAMESPACE = uuid5(NAMESPACE_URL, "crm:notifications:daily-reports")

MANAGER = "manager"
HEAD_OF_SALES = "head_of_sales"
DIRECTOR = "director"


class ReportKind(StrEnum):
    MORNING = "morning"
    EVENING = "evening"


@dataclass(frozen=True, slots=True)
class ReportSchedule:
    morning_enabled: bool = True
    morning_time: time = DEFAULT_MORNING
    evening_enabled: bool = True
    evening_time: time = DEFAULT_EVENING

    def is_due(self, kind: ReportKind, local_now: datetime) -> bool:
        """Due from the configured local time until ``CATCH_UP`` later, the same local day."""
        if kind is ReportKind.MORNING:
            enabled, at = self.morning_enabled, self.morning_time
        else:
            enabled, at = self.evening_enabled, self.evening_time
        if not enabled:
            return False
        start = datetime.combine(local_now.date(), at, tzinfo=local_now.tzinfo)
        return start <= local_now < start + CATCH_UP


@dataclass(frozen=True, slots=True)
class StaffMember:
    user_id: UUID
    name: str
    role: str  # product role: manager | head_of_sales | director | ...
    department_id: UUID | None
    department_name: str = ""


@dataclass(frozen=True, slots=True)
class MorningFigures:
    tasks_due_today: int = 0
    overdue_tasks: int = 0
    meetings_today: int = 0
    deals_without_next_step: int = 0
    # None when the company does not use work sessions at all.
    shift_started: bool | None = None

    def __add__(self, other: MorningFigures) -> MorningFigures:
        return MorningFigures(
            tasks_due_today=self.tasks_due_today + other.tasks_due_today,
            overdue_tasks=self.overdue_tasks + other.overdue_tasks,
            meetings_today=self.meetings_today + other.meetings_today,
            deals_without_next_step=self.deals_without_next_step + other.deals_without_next_step,
        )


@dataclass(frozen=True, slots=True)
class EveningFigures:
    calls: int = 0
    meetings_held: int = 0
    meetings_no_show: int = 0
    tasks_done: int = 0
    deals_moved: int = 0
    deals_lost: int = 0
    payments_recorded: int = 0
    payments_confirmed: int = 0
    # Confirmed payments only, in the company currency (owner rule: only confirmed is money).
    confirmed_amount: Decimal = field(default_factory=lambda: Decimal(0))
    new_leads: int = 0

    @property
    def has_activity(self) -> bool:
        return any(
            (
                self.calls,
                self.meetings_held,
                self.meetings_no_show,
                self.tasks_done,
                self.deals_moved,
                self.deals_lost,
                self.payments_recorded,
                self.payments_confirmed,
                self.new_leads,
            )
        )

    def __add__(self, other: EveningFigures) -> EveningFigures:
        return EveningFigures(
            calls=self.calls + other.calls,
            meetings_held=self.meetings_held + other.meetings_held,
            meetings_no_show=self.meetings_no_show + other.meetings_no_show,
            tasks_done=self.tasks_done + other.tasks_done,
            deals_moved=self.deals_moved + other.deals_moved,
            deals_lost=self.deals_lost + other.deals_lost,
            payments_recorded=self.payments_recorded + other.payments_recorded,
            payments_confirmed=self.payments_confirmed + other.payments_confirmed,
            confirmed_amount=self.confirmed_amount + other.confirmed_amount,
            new_leads=self.new_leads + other.new_leads,
        )


class MessageKind(StrEnum):
    MORNING = "morning"
    EVENING = "evening"
    INACTIVITY = "inactivity"


@dataclass(frozen=True, slots=True)
class ReportMessage:
    kind: MessageKind
    recipient_user_id: UUID
    day: date
    title: str
    body: str
    link: str

    @property
    def key(self) -> UUID:
        """Idempotency key: one message of a kind per recipient per company-local day."""
        return uuid5(_KEY_NAMESPACE, f"{self.kind}:{self.day}:{self.recipient_user_id}")


# --- texts -----------------------------------------------------------------------------


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _money(amount: Decimal, currency: str) -> str:
    whole = f"{amount.quantize(Decimal(1)):,}".replace(",", " ")
    return f"{whole} {currency}"


def _names(people: Iterable[StaffMember]) -> str:
    return ", ".join(sorted(p.name for p in people))


def _morning_line(f: MorningFigures) -> str:
    return (
        f"задачи на сегодня {f.tasks_due_today}, просрочено {f.overdue_tasks}, "
        f"встречи {f.meetings_today}, сделки без следующего шага {f.deals_without_next_step}"
    )


def _evening_line(f: EveningFigures, currency: str) -> str:
    return (
        f"звонки {f.calls}, встречи проведено {f.meetings_held} / не пришли "
        f"{f.meetings_no_show}, задачи выполнены {f.tasks_done}, сделки сдвинуты "
        f"{f.deals_moved} / проиграны {f.deals_lost}, оплаты внесено {f.payments_recorded} / "
        f"подтверждено {f.payments_confirmed} на {_money(f.confirmed_amount, currency)}, "
        f"новые лиды {f.new_leads}"
    )


NO_ACTIVITY = "активности не зафиксировано: ни звонков, ни встреч, ни задач, ни сделок, ни оплат"


@dataclass(frozen=True, slots=True)
class ReportInput:
    day: date
    currency: str
    staff: list[StaffMember]
    absent: set[UUID]
    morning: dict[UUID, MorningFigures] = field(default_factory=dict)
    evening: dict[UUID, EveningFigures] = field(default_factory=dict)


def _present(people: Iterable[StaffMember], absent: set[UUID]) -> list[StaffMember]:
    return [p for p in people if p.user_id not in absent]


def _managers(data: ReportInput) -> list[StaffMember]:
    return [p for p in data.staff if p.role == MANAGER]


def _by_department(managers: list[StaffMember]) -> dict[UUID | None, list[StaffMember]]:
    groups: dict[UUID | None, list[StaffMember]] = {}
    for m in sorted(managers, key=lambda p: p.name):
        groups.setdefault(m.department_id, []).append(m)
    return groups


def _department_label(members: list[StaffMember]) -> str:
    name = members[0].department_name if members else ""
    return f"«{name}»" if name else "без отдела"


def morning_messages(data: ReportInput) -> list[ReportMessage]:
    day = f"{data.day:%d.%m}"
    out: list[ReportMessage] = []
    managers = _managers(data)
    present = _present(managers, data.absent)
    empty = MorningFigures()

    for m in present:
        f = data.morning.get(m.user_id, empty)
        body = (
            f"Задачи на сегодня: {f.tasks_due_today} · Просрочено: {f.overdue_tasks} · "
            f"Встречи сегодня: {f.meetings_today} · Сделки без следующего шага: "
            f"{f.deals_without_next_step}"
        )
        out.append(
            ReportMessage(
                MessageKind.MORNING,
                m.user_id,
                data.day,
                f"Утренний отчёт на {day}",
                body,
                "/my-day",
            )
        )

    def not_started(group: list[StaffMember]) -> list[StaffMember]:
        return [p for p in group if data.morning.get(p.user_id, empty).shift_started is False]

    groups = _by_department(managers)
    for head in _present((p for p in data.staff if p.role == HEAD_OF_SALES), data.absent):
        team = groups.get(head.department_id) if head.department_id else None
        if not team:
            continue
        here = _present(team, data.absent)
        total = sum((data.morning.get(p.user_id, empty) for p in here), MorningFigures())
        parts = [f"Итого по отделу: {_morning_line(total)}."]
        parts += [f"{p.name}: {_morning_line(data.morning.get(p.user_id, empty))}." for p in here]
        if late := not_started(here):
            parts.append(f"Не начали смену: {_names(late)}.")  # noqa: RUF001
        if away := [p for p in team if p.user_id in data.absent]:
            parts.append(f"Отсутствуют: {_names(away)}.")
        out.append(
            ReportMessage(
                MessageKind.MORNING,
                head.user_id,
                data.day,
                f"Утренний отчёт отдела {_department_label(team)} на {day}",
                " ".join(parts),
                "/team",
            )
        )

    if present:
        total = sum((data.morning.get(p.user_id, empty) for p in present), MorningFigures())
        parts = [f"Итого по компании ({_managers_count(present)}): {_morning_line(total)}."]
        for team in groups.values():
            here = _present(team, data.absent)
            if not here:
                continue
            dept = sum((data.morning.get(p.user_id, empty) for p in here), MorningFigures())
            parts.append(f"Отдел {_department_label(team)}: {_morning_line(dept)}.")
        if late := not_started(present):
            parts.append(f"Не начали смену: {_names(late)}.")  # noqa: RUF001
        for director in _present((p for p in data.staff if p.role == DIRECTOR), data.absent):
            out.append(
                ReportMessage(
                    MessageKind.MORNING,
                    director.user_id,
                    data.day,
                    f"Утренний отчёт компании на {day}",
                    " ".join(parts),
                    "/team",
                )
            )
    return [_fit(m) for m in out]


def evening_messages(data: ReportInput) -> list[ReportMessage]:
    day = f"{data.day:%d.%m}"
    out: list[ReportMessage] = []
    managers = _managers(data)
    present = _present(managers, data.absent)
    empty = EveningFigures()

    def figures(p: StaffMember) -> EveningFigures:
        return data.evening.get(p.user_id, empty)

    out += [
        ReportMessage(
            MessageKind.EVENING,
            m.user_id,
            data.day,
            f"Вечерний отчёт за {day}",
            _evening_body(figures(m), data.currency),
            "/my-day",
        )
        for m in present
    ]

    groups = _by_department(managers)
    heads = _present((p for p in data.staff if p.role == HEAD_OF_SALES), data.absent)
    directors = _present((p for p in data.staff if p.role == DIRECTOR), data.absent)
    covered: set[UUID | None] = set()

    for head in heads:
        team = groups.get(head.department_id) if head.department_id else None
        if not team:
            continue
        covered.add(head.department_id)
        here = _present(team, data.absent)
        total = sum((figures(p) for p in here), EveningFigures())
        parts = [f"Итого по отделу: {_evening_line(total, data.currency)}."]
        silent = [p for p in here if not figures(p).has_activity]
        if silent:
            parts.append(f"Без активности: {_names(silent)}.")
        parts += [
            f"{p.name}: {_evening_line(figures(p), data.currency)}."
            for p in here
            if figures(p).has_activity
        ]
        if away := [p for p in team if p.user_id in data.absent]:
            parts.append(f"Отсутствуют: {_names(away)}.")
        label = _department_label(team)
        out.append(
            ReportMessage(
                MessageKind.EVENING,
                head.user_id,
                data.day,
                f"Вечерний отчёт отдела {label} за {day}",
                " ".join(parts),
                "/daily-reports",
            )
        )
        if silent:
            out.append(_inactivity(head.user_id, data.day, silent))

    if present:
        total = sum((figures(p) for p in present), EveningFigures())
        silent = [p for p in present if not figures(p).has_activity]
        parts = [
            f"Итого по компании ({_managers_count(present)}): "
            f"{_evening_line(total, data.currency)}."
        ]
        if silent:
            parts.append(f"Без активности: {_names(silent)}.")
        for team in groups.values():
            here = _present(team, data.absent)
            if here:
                dept = sum((figures(p) for p in here), EveningFigures())
                parts.append(
                    f"Отдел {_department_label(team)}: {_evening_line(dept, data.currency)}."
                )
        # Departments without a (present) head of sales: the director is told instead.
        orphaned = [p for p in silent if p.department_id not in covered]
        for director in directors:
            out.append(
                ReportMessage(
                    MessageKind.EVENING,
                    director.user_id,
                    data.day,
                    f"Вечерний отчёт компании за {day}",
                    " ".join(parts),
                    "/daily-reports",
                )
            )
            if orphaned:
                out.append(_inactivity(director.user_id, data.day, orphaned))
    return [_fit(m) for m in out]


def _evening_body(f: EveningFigures, currency: str) -> str:
    if not f.has_activity:
        return f"За день {NO_ACTIVITY}."  # noqa: RUF001
    return (
        f"Звонки: {f.calls} · Встречи: проведено {f.meetings_held}, не пришли "
        f"{f.meetings_no_show} · Задачи выполнены: {f.tasks_done} · Сделки: сдвинуто "
        f"{f.deals_moved}, проиграно {f.deals_lost} · Оплаты: внесено "
        f"{f.payments_recorded}, подтверждено {f.payments_confirmed} на "
        f"{_money(f.confirmed_amount, currency)} · Новые лиды: {f.new_leads}"
    )


def _inactivity(recipient: UUID, day: date, silent: list[StaffMember]) -> ReportMessage:
    count = len(silent)
    return ReportMessage(
        MessageKind.INACTIVITY,
        recipient,
        day,
        f"Нет активности за {day:%d.%m}: {count} менеджер{_plural(count)}",
        f"{_names(silent)} — за день {NO_ACTIVITY}.",
        "/team",
    )


def _managers_count(people: list[StaffMember]) -> str:
    return f"{len(people)} менеджер{_plural(len(people))}"


def _plural(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return ""
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "а"  # noqa: RUF001
    return "ов"


def _fit(message: ReportMessage) -> ReportMessage:
    return ReportMessage(
        message.kind,
        message.recipient_user_id,
        message.day,
        _clip(message.title, MAX_TITLE),
        _clip(message.body, MAX_BODY),
        message.link,
    )
