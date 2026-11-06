# ruff: noqa: RUF001
"""Requests that change a value, recognised by rule and proposed by the CRM itself.

An amount, a plan target or a person must arrive exactly as asked; a small model may
change a digit or pick the wrong name. So these never pass through it: the CRM reads the
value, shows "was -> becomes", and the owning module runs it after the user confirms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from modules.ai_insights.domain.agent import Subject, Tool


class CommandKind(StrEnum):
    DEAL_AMOUNT = "deal_amount"
    DEAL_OWNER = "deal_owner"
    PLAN_TARGET = "plan_target"
    MEETING_OUTCOME = "meeting_outcome"


@dataclass(frozen=True, slots=True)
class Command:
    kind: CommandKind
    request: str
    number: Decimal | None = None  # the new amount or target
    metric: str | None = None  # PlanMetric value
    month: int | None = None  # the plan's month, when named
    outcome: str | None = None  # held | no_show | cancelled
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class Prepared:
    """A command checked against the data, ready to show for confirmation."""

    tool: Tool
    subject: Subject
    args: dict[str, Any] = field(default_factory=dict)


class CommandRefusedError(Exception):
    """The command cannot be proposed; ``key`` names the message for the user."""

    def __init__(self, key: str, **values: str) -> None:
        super().__init__(key)
        self.key = key
        self.values = values


def change(label: str, before: str, after: str) -> dict[str, str]:
    """What the confirmation card shows as "was -> becomes"."""
    return {"label": label, "before": before, "after": after}


_CHANGE_VERB = re.compile(
    r"\b(измени|поменяй|обнови|поставь|установи|сделай|смени|исправь|подними|снизь"
    r"|update|change|set)\w*",
    re.IGNORECASE,
)
_HAND_OVER = re.compile(r"\b(передай|переназначь|смени|поменяй|назначь|измени|reassign)\w*", re.I)
_NUMBER = r"(\d[\d\s]*(?:[.,]\d{1,2})?)\s*(тыс\w*|млн|k\b|m\b)?"
_NEW_VALUE = re.compile(rf"(?:\bна\b|\bдо\b|\bto\b|=|:)\s*{_NUMBER}", re.IGNORECASE)
_SCALE = {"тыс": 1_000, "k": 1_000, "млн": 1_000_000, "m": 1_000_000}
_MONTHS = {
    "янв": 1,
    "фев": 2,
    "март": 3,
    "апр": 4,
    "май": 5,
    "мая": 5,
    "июн": 6,
    "июл": 7,
    "авг": 8,
    "сент": 9,
    "окт": 10,
    "нояб": 11,
    "дек": 12,
}
_MONTH = re.compile(rf"\b(?:на|за|в)\s+({'|'.join(_MONTHS)})", re.IGNORECASE)
_METRICS = (
    ("оборот", "monthly_turnover"),
    ("turnover", "monthly_turnover"),
    ("предоплат", "weekly_prepayments"),
    ("лид", "weekly_leads"),
    ("звонк", "daily_calls"),
    ("продаж", "monthly_sales"),
    ("sales", "monthly_sales"),
)
_OUTCOMES = (
    ("не приш", "no_show"),
    ("не явил", "no_show"),
    ("no show", "no_show"),
    ("состоял", "held"),
    ("прошла", "held"),
    ("проведен", "held"),
    ("отмен", "cancelled"),
    ("cancel", "cancelled"),
)


def _new_value(text: str) -> Decimal | None:
    found = _NEW_VALUE.findall(text)
    if not found:
        return None
    digits, scale = found[-1]
    try:
        value = Decimal(re.sub(r"\s", "", digits).replace(",", "."))
    except InvalidOperation:
        return None
    return value * _SCALE.get(scale[:3].casefold(), 1)


def _meeting_command(request: str, text: str) -> Command | None:
    if "встреч" not in text or "заметк" in text or "назнач" in text or "перенес" in text:
        return None
    outcome = next((code for stem, code in _OUTCOMES if stem in text), None)
    if outcome is None or text.rstrip().endswith("?"):
        return None
    _, colon, comment = request.partition(":")
    return Command(
        CommandKind.MEETING_OUTCOME,
        request,
        outcome=outcome,
        comment=comment.strip()[:1000] if colon and comment.strip() else None,
    )


def command_in(request: str) -> Command | None:
    text = request.casefold()
    number = _new_value(text)
    if "сумм" in text and _CHANGE_VERB.search(text) and number is not None:
        return Command(CommandKind.DEAL_AMOUNT, request, number=number)
    if "ответственн" in text and _HAND_OVER.search(text):
        return Command(CommandKind.DEAL_OWNER, request)
    if "передай" in text and "сделк" in text:
        return Command(CommandKind.DEAL_OWNER, request)
    if re.search(r"\bплан\w*|\bplan\b", text) and _CHANGE_VERB.search(text) and number is not None:
        metric = next((code for stem, code in _METRICS if stem in text), "monthly_sales")
        month = _MONTH.search(text)
        return Command(
            CommandKind.PLAN_TARGET,
            request,
            number=number,
            metric=metric,
            month=_MONTHS[month.group(1)] if month else None,
        )
    return _meeting_command(request, text)
