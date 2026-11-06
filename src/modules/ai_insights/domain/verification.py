# ruff: noqa: RUF001 - Russian text and patterns by design
"""Deterministic checks of what the model wrote, before anyone sees it.

The model interprets; it does not establish facts. The CRM knows dates, counts and statuses, so
a statement that contradicts them, or that the model cannot have known, is removed, and what
the CRM can state itself (the next task) is stated by the CRM. What was removed is counted in
``verification``; nothing is rewritten by a model and nothing is invented here. A sentence the
patterns do not recognise passes: this narrows the model's errors, it does not prove the rest.
"""

from __future__ import annotations

import re
from typing import Any

# The Platform's notice for a call with nothing to summarise.
INSUFFICIENT_SUMMARY = (
    "Недостаточно данных: в заметках звонка нет подтверждённых фактов для резюме."
)

_OVERDUE_CLAIM = re.compile(
    r"просроч|истек|истёк|истекл|срок\w*\s+(?:уже\s+)?прош|пропущен\w*\s+срок|опозда", re.IGNORECASE
)
_REPEAT_CLAIM = re.compile(
    r"\b(?:дважды|трижды|неоднократн\w*|многократн\w*|несколько раз|\d+\s+раз\w*"
    r"|(?:два|две|три|четыре|пять)\s+раз\w*)\b",
    re.IGNORECASE,
)
_NO_TASKS_CLAIM = re.compile(
    r"(?:отсутств\w*|нет|без)\s+(?:\w+\s+){0,3}(?:задач|действи|следующ\w+\s+шаг)", re.IGNORECASE
)
_NO_CLOSE_DATE_CLAIM = re.compile(
    r"(?:отсутств\w*|нет|не указан\w*|не запланирован\w*)\s+(?:\w+\s+){0,3}дат\w*\s+закрыт",
    re.IGNORECASE,
)
_CLOSE_PASSED_CLAIM = re.compile(
    r"дат\w*\s+закрыт\w*\s+(?:\w+\s+){0,2}(?:прошл|истек|истёк)", re.IGNORECASE
)
_COUNT_CLAIM = re.compile(
    r"\b(\d+|одн[аоу]|один|две|два|три|четыре|пять)\s+(встреч|звонк|звонок|задач|заметк|заметок|перенос)",
    re.IGNORECASE,
)
_NUMBER = r"(\d+|одн[аоу]|один|две|два|три|четыре|пять)"
# "2 состоятся", "из которых две пройдут": how many meetings are said to be still ahead.
_PLANNED_COUNT = re.compile(
    rf"\b{_NUMBER}\s+(?:\w+\s+)?(?:состо\w+тся|пройд\w+|предстоит)", re.IGNORECASE
)
_PLANNED = re.compile(r"состоится|состоятся|запланирован|предстоит|пройд[её]т", re.IGNORECASE)
_WORDS = {
    "один": 1,
    "одна": 1,
    "одно": 1,
    "одну": 1,
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
}
_COUNTED = {
    "встреч": "meetings",
    "звонк": "calls",
    "звонок": "calls",
    "задач": "open_tasks",
    "заметк": "notes",
    "заметок": "notes",
    "перенос": "reschedules",
}
# Service words of the input leaking into a sentence: (expected_close_date null), 'open'.
_LEAK = re.compile(r"\s*[(:]?\s*\b[a-z]+(?:_[a-z]+)+\b(?::?\s*null)?\s*\)?|\s*\(?\bnull\b\)?")
_LEAK_WORD = re.compile(r"[a-z]+_[a-z]+|\bnull\b|'(?:open|done|overdue|scheduled|completed)'")

_STOPWORDS = {"клиент", "клиента", "клиенту", "менеджер", "стороны", "этот", "этой", "который"}
_MIN_WORD = 4
_STEM = 5
_MIN_SUPPORT = 0.34
_NEXT_MARKERS = re.compile(
    r"договорил|перезвон|созвон|назначил|отправл|пришл[юе]|жду|вернёмс|вернемс|встретимся",
    re.IGNORECASE,
)


_NO_AGREEMENT = re.compile(
    r"\bне\s+(?:\w+\s+)?(?:договор|дозвон|решил|смог|удалось|ответил)|ни о ч[её]м", re.IGNORECASE
)


def _stems(text: str) -> set[str]:
    words = re.findall(r"[^\W\d_]+", text.lower().replace("ё", "е"))
    return {w[:_STEM] for w in words if len(w) >= _MIN_WORD and w not in _STOPWORDS}


def quote_supports(text: str, quote: str) -> bool:
    """Whether a quote is about what the point says: enough of the point's words are in it."""
    point = _stems(text)
    return bool(point) and len(point & _stems(quote)) / len(point) >= _MIN_SUPPORT


def _strip_leaks(text: str) -> str:
    return " ".join(_LEAK.sub("", text).split())


def _wrong_count(text: str, checks: dict[str, Any]) -> bool:
    for number, noun in _COUNT_CLAIM.findall(text):
        count = int(number) if number.isdigit() else _WORDS[number.lower()]
        if count != checks[_COUNTED[noun.lower()]]:
            return True
    return False


def _wrong_about_meetings(text: str, checks: dict[str, Any]) -> bool:
    """Meetings still ahead, by number or by name, against what the CRM has planned."""
    for number in _PLANNED_COUNT.findall(text):
        count = int(number) if number.isdigit() else _WORDS[number.lower()]
        if count != checks["meetings_upcoming"]:
            return True
    if not _PLANNED.search(text):
        return False
    return any(title and title in text for title in checks.get("finished_meetings", ()))


def _contradicts(text: str, checks: dict[str, Any]) -> bool:
    nothing_late = not checks["overdue_tasks"] and not checks["close_date_passed"]
    return bool(
        (_OVERDUE_CLAIM.search(text) and nothing_late)
        or _REPEAT_CLAIM.search(text)
        or (_NO_TASKS_CLAIM.search(text) and checks["open_tasks"])
        or (_NO_CLOSE_DATE_CLAIM.search(text) and checks["expected_close_date"])
        or (_CLOSE_PASSED_CLAIM.search(text) and not checks["close_date_passed"])
        or _wrong_count(text, checks)
        or _wrong_about_meetings(text, checks)
    )


def _clean_points(
    points: list[dict[str, Any]], checks: dict[str, Any]
) -> tuple[list[dict[str, Any]], int]:
    kept: list[dict[str, Any]] = []
    dropped = 0
    for point in points:
        text = _strip_leaks(str(point.get("text", "")))
        if not text or _LEAK_WORD.search(text) or _contradicts(text, checks):
            dropped += 1
        else:
            kept.append({**point, "text": text})
    return kept, dropped


def _crm_next_step(checks: dict[str, Any]) -> dict[str, Any] | None:
    meeting, task = checks.get("next_meeting"), checks.get("next_task")
    if meeting and (not task or meeting["date"] <= task["date"]):
        return {
            "text": f"Провести встречу «{meeting['title']}» {meeting['date']}",
            "refs": [meeting["ref"]],
        }
    if task:
        return {
            "text": f"Выполнить задачу «{task['title']}», срок {task['date']}",
            "refs": [task["ref"]],
        }
    return None


def verify_brief(result: dict[str, Any], checks: dict[str, Any]) -> dict[str, Any]:
    """The brief without statements that contradict the CRM, with the CRM's next step."""
    verified = dict(result)
    dropped = 0
    for section in ("situation", "risks", "questions"):
        verified[section], removed = _clean_points(result.get(section) or [], checks)
        dropped += removed
    model_step = result.get("next_step")
    steps, removed = _clean_points([model_step] if model_step else [], checks)
    dropped += removed
    crm_step = _crm_next_step(checks)
    # A real task or meeting beats a model's idea of what to do next.
    verified["next_step"] = crm_step or (steps[0] if steps else None)
    if not verified["situation"]:
        verified["situation"] = [
            {
                "text": f"Сделка на этапе «{checks['stage']}». Остальное подтвердить не удалось.",
                "refs": [checks["deal_ref"]],
            }
        ]
    verified["verification"] = {"dropped": dropped, "next_step_from_crm": crm_step is not None}
    return verified


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def verify_call_summary(result: dict[str, Any], text: str) -> dict[str, Any]:
    """Points whose quote does not back them are removed; the summary, which nothing checks,
    is replaced by the confirmed points when any of them failed. An agreement written in the
    notes becomes the next step, word for word, when the model missed it."""
    if result.get("insufficient_data"):
        return result
    verified = dict(result)
    points = result.get("key_points") or []
    kept = [p for p in points if quote_supports(p["text"], p["evidence_quote"])]
    dropped = len(points) - len(kept)
    step = result.get("next_step")
    if step and not quote_supports(step["text"], step["evidence_quote"]):
        step = None
        dropped += 1
    from_notes = False
    if step is None:
        agreed = [
            s for s in _sentences(text) if _NEXT_MARKERS.search(s) and not _NO_AGREEMENT.search(s)
        ]
        if agreed:
            step = {"text": agreed[-1], "evidence_quote": agreed[-1]}
            from_notes = True
    verified["key_points"], verified["next_step"] = kept, step
    if dropped:
        verified["summary"] = " ".join(p["text"] for p in kept) or INSUFFICIENT_SUMMARY
    if dropped and not kept and step is None:
        verified["summary"], verified["insufficient_data"] = INSUFFICIENT_SUMMARY, True
    verified["verification"] = {"dropped": dropped, "next_step_from_notes": from_notes}
    return verified
