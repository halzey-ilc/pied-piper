# ruff: noqa: RUF001
"""Questions about CRM data, recognised by rule and answered by the CRM itself.

A small model miscounts and cannot see the user's rights, so numbers never come from
it: the owning modules count what this user may see, and no model is called.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Question(StrEnum):
    LEADS_COUNT = "leads_count"
    DEALS_COUNT = "deals_count"
    OVERDUE_DEALS = "overdue_deals"
    OVERDUE_TASKS = "overdue_tasks"
    PLAN_PROGRESS = "plan_progress"


@dataclass(frozen=True, slots=True)
class AnswerItem:
    title: str
    ref: str | None = None  # the record it links to, e.g. "deal:<uuid>"


@dataclass(frozen=True, slots=True)
class DataAnswer:
    summary: str
    items: tuple[AnswerItem, ...] = ()


_ASKING = (
    "сколько",
    "какие",
    "какая",
    "кто ",
    "покажи",
    "есть ли",
    "список",
    "how many",
    "which",
    "who ",
    "show",
    "list",
    "канча",
    "кайсы",
    "ким ",
)
# A request to change something is for the planner, whatever it mentions.
_CHANGING = (
    "обнови",
    "измени",
    "поменяй",
    "установи",
    "передай",
    "смени",
    "поставь",
    "запиши",
    "назначь",
    "перенеси",
    "создай",
    "удали",
    "update",
    "change",
    "create",
    "delete",
    "move",
    "schedule",
)
_LEADS = ("лид", "lead")
_DEALS = ("сделк", "сделок", "deal", "бүтүм")
_TASKS = ("задач", "task", "тапшырма")
_OVERDUE = ("просроч", "overdue", "мөөнөтү өткөн")
_PLAN = ("план", "plan")


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def question_in(request: str) -> Question | None:
    text = f"{request.casefold()} "
    if _has(text, _CHANGING) or not _has(text, _ASKING):
        return None
    if _has(text, _OVERDUE):
        if _has(text, _TASKS):
            return Question.OVERDUE_TASKS
        return Question.OVERDUE_DEALS if _has(text, _DEALS) else None
    if _has(text, _PLAN):
        return Question.PLAN_PROGRESS
    if _has(text, _LEADS):
        return Question.LEADS_COUNT
    return Question.DEALS_COUNT if _has(text, _DEALS) else None


_ANSWERS: dict[str, dict[str, str]] = {
    "ru": {
        Question.LEADS_COUNT: "Лидов, которые вам видны: {count}.",
        Question.DEALS_COUNT: "Открытых сделок, которые вам видны: {count}.",
        Question.OVERDUE_DEALS: "Просроченных сделок: {count}.",
        Question.OVERDUE_TASKS: "Просроченных задач: {count}.",
        Question.PLAN_PROGRESS: "Планов на текущий период: {count}.",
        "no_plans": "Планов на текущий период, которые вам видны, нет.",
        "no_access": "У вашей роли нет доступа к этим данным.",
    },
    "ky": {
        Question.LEADS_COUNT: "Сизге көрүнгөн лиддер: {count}.",
        Question.DEALS_COUNT: "Сизге көрүнгөн ачык бүтүмдөр: {count}.",
        Question.OVERDUE_DEALS: "Мөөнөтү өткөн бүтүмдөр: {count}.",
        Question.OVERDUE_TASKS: "Мөөнөтү өткөн тапшырмалар: {count}.",
        Question.PLAN_PROGRESS: "Учурдагы мезгилдин пландары: {count}.",
        "no_plans": "Учурдагы мезгил үчүн сизге көрүнгөн план жок.",
        "no_access": "Ролуңузда бул маалыматка кирүү жок.",
    },
    "en": {
        Question.LEADS_COUNT: "Leads you can see: {count}.",
        Question.DEALS_COUNT: "Open deals you can see: {count}.",
        Question.OVERDUE_DEALS: "Overdue deals: {count}.",
        Question.OVERDUE_TASKS: "Overdue tasks: {count}.",
        Question.PLAN_PROGRESS: "Plans for the current period: {count}.",
        "no_plans": "There are no plans you can see for the current period.",
        "no_access": "Your role has no access to this data.",
    },
}


def answer_text(language: str, key: str, count: int = 0) -> str:
    return _ANSWERS[language][key].format(count=count)
