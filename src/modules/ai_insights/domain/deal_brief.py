"""The DealBrief aggregate: AI preparation for the next conversation about a deal
(AI Sales Coach).

The CRM collects the deal and its history with the requesting user's own
rights and computes dates and statuses (e.g. which tasks are overdue); the AI
Platform only writes the brief, citing those records. The input is kept until
it has been submitted and then cleared, so copies of notes do not accumulate.

pending -> submitted -> succeeded | failed
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, ClassVar
from uuid import UUID, uuid4

from modules.ai_insights.domain.insight import InsightStatus
from modules.ai_insights.domain.redaction import mask_personal_data
from modules.ai_insights.domain.report import payload_fingerprint, validate_language
from modules.ai_insights.domain.verification import verify_brief
from shared.domain.errors import DomainValidationError
from shared.domain.events import DomainEvent

JOB_TYPE = "deal.coach.v1"
OUTPUT_SCHEMA = "deal_coach.output.v1"
MAX_HISTORY = 40
MAX_TEXT = 2000
# The input holds at most MAX_HISTORY entries; one is the CRM's own facts.
FACTS_PREFIX = "facts"


@dataclass(frozen=True, slots=True)
class DealFacts:
    id: UUID
    title: str
    status: str  # open | won | lost
    stage: str
    stage_position: int
    stage_count: int
    probability: int
    amount: float
    currency: str
    expected_close_date: date | None
    created_on: date


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    ref: str  # "activity:<uuid>", "meeting:<uuid>", "call:<uuid>"
    kind: str  # note | task | call | meeting
    on: date
    title: str | None
    text: str | None
    # task: open | done | cancelled | overdue; meeting: scheduled | completed | cancelled
    status: str | None
    # What the contract's three meeting states cannot say: "клиент не пришёл", "перенесена".
    detail: str | None = None


def task_status(*, completed: bool, due: date | None, today: date, cancelled: bool = False) -> str:
    """Overdue is decided here, by the CRM, not by the model."""
    if cancelled:
        return "cancelled"
    if completed:
        return "done"
    return "overdue" if due is not None and due < today else "open"


# deal_coach.input.v1 knows three meeting states; the CRM's statuses map onto them.
_MEETING_STATUS = {
    "scheduled": "scheduled",
    "rescheduled": "scheduled",
    "held": "completed",
    "completed": "completed",
    "cancelled": "cancelled",
    "no_show": "cancelled",  # it did not take place
}


def meeting_status(status: str) -> str:
    return _MEETING_STATUS[status]


def _clip(text: str | None, limit: int, names: Iterable[str] = ()) -> str | None:
    if text is None:
        return None
    cleaned = mask_personal_data(text, names).strip()
    return cleaned[:limit] or None


def _days(count: int) -> str:
    return f"{count} дн."


def _timing(entry: HistoryEntry, today: date) -> str | None:
    """What a date means today, in words: the model misreads bare dates."""
    on = entry.on.isoformat()
    if entry.kind == "task" and entry.status == "overdue":
        return f"Срок {on} прошёл, просрочена на {_days((today - entry.on).days)}"
    if entry.kind == "task" and entry.status == "open" and entry.on >= today:
        return f"Срок {on} ещё не наступил, осталось {_days((entry.on - today).days)}"
    if entry.kind == "meeting":
        when = "в будущем" if entry.on > today else "в прошлом" if entry.on < today else "сегодня"
        return f"Дата встречи {when}" + (f", {entry.detail}" if entry.detail else "")
    return None


def _with_timing(entry: HistoryEntry, today: date, names: Iterable[str]) -> str | None:
    timing = _timing(entry, today)
    text = _clip(entry.text, MAX_TEXT, names)
    if timing is None:
        return text
    return f"{timing}. {text}"[:MAX_TEXT] if text else timing


def deal_checks(
    deal: DealFacts, history: list[HistoryEntry], today: date, *, reschedules: int = 0
) -> dict[str, Any]:
    """The deal's facts, counted by the CRM: what the brief is told and checked against."""
    tasks = [h for h in history if h.kind == "task"]
    meetings = [h for h in history if h.kind == "meeting"]
    open_tasks = sorted(
        (t for t in tasks if t.status in ("open", "overdue")), key=lambda t: (t.on, t.ref)
    )
    upcoming = sorted(
        (m for m in meetings if m.status == "scheduled" and m.on >= today), key=lambda m: m.on
    )
    next_task = next((t for t in open_tasks if t.on >= today), None) or (
        open_tasks[0] if open_tasks else None
    )
    return {
        "today": today.isoformat(),
        "deal_ref": f"deal:{deal.id}",
        "stage": deal.stage,
        "expected_close_date": (
            deal.expected_close_date.isoformat() if deal.expected_close_date else None
        ),
        "close_date_passed": bool(deal.expected_close_date and deal.expected_close_date < today),
        "open_tasks": len(open_tasks),
        "overdue_tasks": sum(1 for t in tasks if t.status == "overdue"),
        "meetings": len(meetings),
        "meetings_held": sum(1 for m in meetings if m.status == "completed"),
        "meetings_upcoming": len(upcoming),
        # A title shared with a meeting still ahead says nothing about which one is meant.
        "finished_meetings": sorted(
            {m.title for m in meetings if m.title and m.status != "scheduled"}
            - {m.title for m in upcoming}
        ),
        "calls": sum(1 for h in history if h.kind == "call"),
        "notes": sum(1 for h in history if h.kind == "note"),
        "reschedules": reschedules,
        "next_task": (
            {"ref": next_task.ref, "title": next_task.title or "", "date": next_task.on.isoformat()}
            if next_task
            else None
        ),
        "next_meeting": (
            {
                "ref": upcoming[0].ref,
                "title": upcoming[0].title or "",
                "date": upcoming[0].on.isoformat(),
            }
            if upcoming
            else None
        ),
    }


def _facts_text(checks: dict[str, Any]) -> str:
    """The CRM's own count of what happened; the model is told not to recount."""
    close = checks["expected_close_date"] or "не указана"
    state = ", уже прошла" if checks["close_date_passed"] else ""
    return (
        f"Факты CRM на {checks['today']}, считать только по ним: "
        f"открытых задач {checks['open_tasks']}, просроченных {checks['overdue_tasks']}; "
        f"встреч {checks['meetings']}, состоялось {checks['meetings_held']}, "
        f"впереди {checks['meetings_upcoming']}, переносов {checks['reschedules']}; "
        f"звонков {checks['calls']}, заметок {checks['notes']}; "
        f"дата закрытия {close}{state}. Каждое событие произошло ровно столько раз, сколько "
        "здесь указано. Если чего-то нет в истории, этого не было."
    )


def build_payload(
    *,
    language: str,
    today: date,
    deal: DealFacts,
    history: list[HistoryEntry],
    checks: dict[str, Any] | None = None,
    names: Iterable[str] = (),
) -> dict[str, Any]:
    """The job input: the CRM's facts, then the deal's latest history, oldest first."""
    facts = checks or deal_checks(deal, history, today)
    recent = sorted(history, key=lambda h: (h.on, h.ref))[-(MAX_HISTORY - 1) :]
    facts_entry = {
        "ref": f"{FACTS_PREFIX}:{deal.id}",
        "kind": "note",
        "date": today.isoformat(),
        "title": "Факты CRM",
        "text": _facts_text(facts),
        "status": None,
    }
    return {
        "language": validate_language(language),
        "today": today.isoformat(),
        "deal": {
            "ref": f"deal:{deal.id}",
            "title": mask_personal_data(deal.title, names)[:200],
            "status": deal.status,
            "stage": deal.stage[:100],
            "stage_position": deal.stage_position,
            "stage_count": deal.stage_count,
            "probability": deal.probability,
            "amount": deal.amount,
            "currency": deal.currency,
            "expected_close_date": deal.expected_close_date.isoformat()
            if deal.expected_close_date
            else None,
            "age_days": max(0, (today - deal.created_on).days),
        },
        # Without history there is nothing to interpret: the Platform reports that itself.
        "history": [
            *([facts_entry] if recent else []),
            *[
                {
                    "ref": h.ref,
                    "kind": h.kind,
                    "date": h.on.isoformat(),
                    "title": _clip(h.title, 200, names),
                    "text": _with_timing(h, today, names),
                    "status": h.status,
                }
                for h in recent
            ],
        ],
    }


def compact_refs(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Short refs for the model, and the map back to the CRM's refs.

    Every point of a brief repeats the refs it cites, and a UUID costs the model about 25
    tokens. With real ids the local model ran out of its output budget and the JSON was cut
    off (measured on Qwen3.5-4B: 0 of 4 briefs with UUIDs, 2 of 2 with short refs).
    """
    numbers: dict[str, int] = {}
    refs: dict[str, str] = {}

    def short(ref: str) -> str:
        prefix = ref.split(":", 1)[0]
        numbers[prefix] = numbers.get(prefix, 0) + 1
        alias = f"{prefix}:{numbers[prefix]}"
        # The facts entry is not a record: a point citing it cites the deal.
        refs[alias] = f"deal:{ref.split(':', 1)[1]}" if prefix == FACTS_PREFIX else ref
        return alias

    compact = {
        **payload,
        "deal": {**payload["deal"], "ref": short(payload["deal"]["ref"])},
        "history": [{**entry, "ref": short(entry["ref"])} for entry in payload["history"]],
    }
    return compact, refs


def restore_refs(result: dict[str, Any], refs: dict[str, str]) -> dict[str, Any]:
    """The brief with the CRM's own refs, so that clients can link to the records."""

    def point(item: dict[str, Any]) -> dict[str, Any]:
        return {**item, "refs": [refs.get(ref, ref) for ref in item.get("refs", [])]}

    restored = dict(result)
    for section in ("situation", "risks", "questions"):
        restored[section] = [point(item) for item in result.get(section) or []]
    if result.get("next_step"):
        restored["next_step"] = point(result["next_step"])
    return restored


@dataclass(frozen=True, kw_only=True)
class DealBriefCompleted(DomainEvent):
    event_type: ClassVar[str] = "ai_insights.deal_brief_completed"

    brief_id: UUID
    deal_id: UUID
    requested_by: UUID
    succeeded: bool

    def payload(self) -> dict[str, Any]:
        return {
            "brief_id": str(self.brief_id),
            "deal_id": str(self.deal_id),
            "requested_by": str(self.requested_by),
            "succeeded": self.succeeded,
        }


@dataclass
class DealBrief:
    id: UUID
    deal_id: UUID
    requested_by: UUID
    payload: dict[str, Any] | None
    source_hash: str
    status: InsightStatus
    created_at: datetime
    ai_job_id: str | None = None
    result: dict[str, Any] | None = None
    provider: str | None = None
    model: str | None = None
    is_mock: bool = False
    error_code: str | None = None
    finished_at: datetime | None = None
    version: int = 1
    # Short refs sent to the model -> the CRM's refs; ids only, kept until the result is in.
    ref_map: dict[str, str] | None = None
    # The CRM's facts at request time: the result is checked against them, then they go.
    checks: dict[str, Any] | None = None

    @classmethod
    def request(
        cls,
        *,
        deal_id: UUID,
        requested_by: UUID,
        payload: dict[str, Any],
        checks: dict[str, Any] | None = None,
    ) -> DealBrief:
        validate_language(str(payload.get("language")))
        return cls(
            id=uuid4(),
            deal_id=deal_id,
            requested_by=requested_by,
            payload=payload,
            source_hash=payload_fingerprint(payload),
            status=InsightStatus.PENDING,
            created_at=datetime.now(UTC),
            checks=checks,
        )

    @property
    def is_finished(self) -> bool:
        return self.status in (InsightStatus.SUCCEEDED, InsightStatus.FAILED)

    def mark_submitted(self, ai_job_id: str, ref_map: dict[str, str] | None = None) -> None:
        if self.status is not InsightStatus.PENDING or not ai_job_id:
            raise DomainValidationError("Only a pending brief can be submitted.")
        self.ai_job_id = ai_job_id
        self.ref_map = ref_map
        self.status = InsightStatus.SUBMITTED
        self.payload = None  # the AI Platform has it now; keep only the fingerprint

    def mark_succeeded(
        self, *, result: dict[str, Any], schema_id: str, provider: str, model: str, is_mock: bool
    ) -> DealBriefCompleted:
        if self.status is not InsightStatus.SUBMITTED:
            raise DomainValidationError("Only a submitted brief can succeed.")
        if schema_id != OUTPUT_SCHEMA:
            raise DomainValidationError("The AI result uses an unsupported output contract.")
        restored = restore_refs(result, self.ref_map or {})
        self.result = verify_brief(restored, self.checks) if self.checks else restored
        self.ref_map = None
        self.checks = None
        self.provider = provider
        self.model = model
        self.is_mock = is_mock
        self.status = InsightStatus.SUCCEEDED
        self.finished_at = datetime.now(UTC)
        return self._completed(succeeded=True)

    def mark_failed(self, error_code: str) -> DealBriefCompleted:
        if self.is_finished:
            raise DomainValidationError("The brief is already finished.")
        self.error_code = error_code[:64]
        self.status = InsightStatus.FAILED
        self.payload = None
        self.ref_map = None
        self.checks = None
        self.finished_at = datetime.now(UTC)
        return self._completed(succeeded=False)

    def _completed(self, *, succeeded: bool) -> DealBriefCompleted:
        return DealBriefCompleted(
            brief_id=self.id,
            deal_id=self.deal_id,
            requested_by=self.requested_by,
            succeeded=succeeded,
        )
