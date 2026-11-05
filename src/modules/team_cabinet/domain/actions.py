"""«Что делать сегодня»: the rules-based list of priority actions for a head of sales or
a director (spec 14.2, 14.3, 14.10/14.16, 17.3). Pure Python, no AI.

Each group has a stable ``kind`` code, a fixed priority (the order below) and a plain-language
rule. Groups are always returned, also when empty, so the client can show an empty state.

* ``payments_to_verify``      - payments waiting for verification (``pending_verification``);
* ``overdue_tasks``           - open tasks whose due date has passed;
* ``tasks_due_soon``          - open tasks due today or tomorrow (17.3, approaching deadline);
* ``meetings_without_outcome``- scheduled meetings that already ended with no outcome;
* ``deals_without_next_step`` - open deals with no open task with a due date and no
  upcoming scheduled meeting;
* ``deals_to_push``           - «дожим»: open deals on the same stage for at least the stalled
  threshold that had a held meeting, a confirmed prepayment or an unpaid balance;
* ``leads_without_contact``   - new leads with no first response after the response threshold;
* ``managers_without_activity`` - managers (sales, lead callers) not on a planned absence who
  did not start a shift today or have no calls, meetings or payments today.

A sales manager's own «Фокус» (owner decision of 05.11.2026) uses the same rules for their own
records, plus two groups of its own:

* ``miss_tasks``       - open tasks the head of sales gave on the manager's mistakes
  («Работа над ошибками», task type ``review``); first in the list;
* ``debts_to_collect`` - open or won deals with confirmed money below the deal amount.

Money: only confirmed payments count as paid; sums are kept per currency, never added
across currencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

# A lead counts as answered when it got a first response within this many minutes; the
# cabinet's lead-response figures use the same bucket (within_15_min).
LEAD_RESPONSE_MINUTES = 15
DEFAULT_ACTIONS_LIMIT = 5
MAX_ACTIONS_LIMIT = 50
# Who is expected to be on shift and active: sales managers and lead callers.
FIELD_ROLES = ("manager", "lead_caller")


class ActionKind(StrEnum):
    PAYMENTS_TO_VERIFY = "payments_to_verify"
    OVERDUE_TASKS = "overdue_tasks"
    TASKS_DUE_SOON = "tasks_due_soon"
    MEETINGS_WITHOUT_OUTCOME = "meetings_without_outcome"
    DEALS_WITHOUT_NEXT_STEP = "deals_without_next_step"
    DEALS_TO_PUSH = "deals_to_push"
    LEADS_WITHOUT_CONTACT = "leads_without_contact"
    MANAGERS_WITHOUT_ACTIVITY = "managers_without_activity"
    MISS_TASKS = "miss_tasks"
    DEBTS_TO_COLLECT = "debts_to_collect"


# Priority order of the head of sales' list: shown in this order.
ACTION_ORDER: tuple[ActionKind, ...] = (
    ActionKind.PAYMENTS_TO_VERIFY,
    ActionKind.OVERDUE_TASKS,
    ActionKind.TASKS_DUE_SOON,
    ActionKind.MEETINGS_WITHOUT_OUTCOME,
    ActionKind.DEALS_WITHOUT_NEXT_STEP,
    ActionKind.DEALS_TO_PUSH,
    ActionKind.LEADS_WITHOUT_CONTACT,
    ActionKind.MANAGERS_WITHOUT_ACTIVITY,
)

# Priority order of a manager's own «Фокус».
FOCUS_ORDER: tuple[ActionKind, ...] = (
    ActionKind.MISS_TASKS,
    ActionKind.OVERDUE_TASKS,
    ActionKind.DEALS_TO_PUSH,
    ActionKind.DEBTS_TO_COLLECT,
    ActionKind.LEADS_WITHOUT_CONTACT,
    ActionKind.MEETINGS_WITHOUT_OUTCOME,
    ActionKind.DEALS_WITHOUT_NEXT_STEP,
)


class PushReason(StrEnum):
    AFTER_MEETING = "after_meeting"  # a meeting on the deal was held
    PREPAYMENT = "prepayment"  # a confirmed prepayment was received
    DEBT = "debt"  # confirmed money received is below the deal amount


class ManagerReason(StrEnum):
    NOT_ON_SHIFT = "not_on_shift"  # no work session started today
    NO_ACTIVITY = "no_activity"  # no calls, meetings or payments today


@dataclass(frozen=True, slots=True)
class ActionThresholds:
    stalled_deal_days: int
    lead_response_minutes: int = LEAD_RESPONSE_MINUTES


@dataclass(slots=True)
class ActionItem:
    id: UUID
    title: str
    manager_id: UUID | None
    amount: Decimal | None = None
    currency: str | None = None
    date: date | None = None  # task due date or payment date
    at: datetime | None = None  # meeting start, payment record time, lead creation
    age_days: int | None = None
    subject_type: str | None = None  # "deal" | "lead" | "customer"
    subject_id: UUID | None = None
    reasons: tuple[str, ...] = ()
    paid: Decimal | None = None  # confirmed, in the deal currency (deals_to_push)
    debt: Decimal | None = None
    stage_name: str | None = None
    manager_username: str = ""


@dataclass(slots=True)
class ActionGroup:
    kind: ActionKind
    count: int = 0
    items: list[ActionItem] = field(default_factory=list)
    by_manager: dict[UUID, int] = field(default_factory=dict)
    # Money per currency (never summed across currencies); empty when the group has no money.
    amounts: dict[str, Decimal] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TodayActions:
    day: date
    as_of: datetime
    limit: int
    thresholds: ActionThresholds
    department_id: UUID | None
    groups: list[ActionGroup]
    usernames: dict[UUID, str] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(group.count for group in self.groups)


def push_reasons(
    *, held_meeting: bool, prepayment: bool, paid: Decimal, amount: Decimal
) -> tuple[str, ...]:
    """Why an open stalled deal needs pushing; empty when it does not qualify."""
    reasons: list[str] = []
    if held_meeting:
        reasons.append(PushReason.AFTER_MEETING.value)
    if prepayment:
        reasons.append(PushReason.PREPAYMENT.value)
    if Decimal(0) < paid < amount:
        reasons.append(PushReason.DEBT.value)
    return tuple(reasons)


def manager_reasons(*, on_shift: bool, has_activity: bool) -> tuple[str, ...]:
    reasons: list[str] = []
    if not on_shift:
        reasons.append(ManagerReason.NOT_ON_SHIFT.value)
    if not has_activity:
        reasons.append(ManagerReason.NO_ACTIVITY.value)
    return tuple(reasons)


TITLES: dict[ActionKind, str] = {
    ActionKind.PAYMENTS_TO_VERIFY: "Оплаты ждут проверки",
    ActionKind.OVERDUE_TASKS: "Просроченные задачи",
    ActionKind.TASKS_DUE_SOON: "Задачи: срок сегодня и завтра",
    ActionKind.MEETINGS_WITHOUT_OUTCOME: "Встречи без итога",
    ActionKind.DEALS_WITHOUT_NEXT_STEP: "Сделки без следующего шага",
    ActionKind.DEALS_TO_PUSH: "Сделки на дожим",
    ActionKind.LEADS_WITHOUT_CONTACT: "Необработанные лиды",
    ActionKind.MANAGERS_WITHOUT_ACTIVITY: "Менеджеры не на смене или без активности",
    ActionKind.MISS_TASKS: "Задачи по проколам",
    ActionKind.DEBTS_TO_COLLECT: "Собрать долг",
}


def rule_text(kind: ActionKind, thresholds: ActionThresholds) -> str:
    """The rule of a group in plain words (shown to the head of sales)."""
    rules: dict[ActionKind, str] = {
        ActionKind.PAYMENTS_TO_VERIFY: (
            "Оплаты в статусе «на проверке»: их нужно подтвердить или отклонить. "
            "Сумма не входит в выручку, пока оплата не подтверждена."
        ),
        ActionKind.OVERDUE_TASKS: "Открытые задачи, срок которых уже прошёл.",
        ActionKind.TASKS_DUE_SOON: "Открытые задачи, срок которых сегодня или завтра.",
        ActionKind.MEETINGS_WITHOUT_OUTCOME: (
            "Встреча уже закончилась, но итог (состоялась, не пришёл, отмена) не записан."
        ),
        ActionKind.DEALS_WITHOUT_NEXT_STEP: (
            "Открытая сделка без открытой задачи, имеющей срок, и без будущей встречи."
        ),
        ActionKind.DEALS_TO_PUSH: (
            f"Открытая сделка стоит на этапе {thresholds.stalled_deal_days} дн. и дольше, "
            "и по ней была встреча, получена предоплата или остался долг. Деньги — только "
            "подтверждённые оплаты в валюте сделки."
        ),
        ActionKind.LEADS_WITHOUT_CONTACT: (
            f"Новый лид без первого контакта дольше {thresholds.lead_response_minutes} мин.: "
            "ни звонка, ни задачи, ни заметки, статус не менялся."
        ),
        ActionKind.MANAGERS_WITHOUT_ACTIVITY: (
            "Менеджер не в плановом отсутствии и сегодня не начал смену либо не сделал "
            "ни звонка, ни встречи, ни оплаты."
        ),
        ActionKind.MISS_TASKS: (
            "Открытые задачи «Работы над ошибками», которые руководитель поставил по "
            "проколам менеджера. Их делают в первую очередь."
        ),
        ActionKind.DEBTS_TO_COLLECT: (
            "Открытая или выигранная сделка, по которой подтверждённых оплат меньше суммы "
            "сделки: клиент внёс предоплату, остаток нужно собрать. Деньги — только в валюте "
            "сделки."
        ),
    }
    return rules[kind]
