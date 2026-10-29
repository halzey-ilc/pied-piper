"""The DealBrief aggregate: AI preparation for the next conversation about a deal
(AI Sales Coach).

The CRM collects the deal and its history with the requesting user's own
rights and computes dates and statuses (e.g. which tasks are overdue); the AI
Platform only writes the brief, citing those records. The input is kept until
it has been submitted and then cleared, so copies of notes do not accumulate.

pending -> submitted -> succeeded | failed
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, ClassVar
from uuid import UUID, uuid4

from modules.ai_insights.domain.insight import InsightStatus
from modules.ai_insights.domain.report import payload_fingerprint, validate_language
from shared.domain.errors import DomainValidationError
from shared.domain.events import DomainEvent

JOB_TYPE = "deal.coach.v1"
OUTPUT_SCHEMA = "deal_coach.output.v1"
MAX_HISTORY = 40
MAX_TEXT = 2000


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
    status: str | None  # task: open | done | overdue; meeting: scheduled | completed | cancelled


def task_status(*, completed: bool, due: date | None, today: date) -> str:
    """Overdue is decided here, by the CRM, not by the model."""
    if completed:
        return "done"
    return "overdue" if due is not None and due < today else "open"


def _clip(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    cleaned = text.strip()
    return cleaned[:limit] or None


def build_payload(
    *, language: str, today: date, deal: DealFacts, history: list[HistoryEntry]
) -> dict[str, Any]:
    """The job input: the deal and its latest history, oldest first."""
    recent = sorted(history, key=lambda h: (h.on, h.ref))[-MAX_HISTORY:]
    return {
        "language": validate_language(language),
        "today": today.isoformat(),
        "deal": {
            "ref": f"deal:{deal.id}",
            "title": deal.title[:200],
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
        "history": [
            {
                "ref": h.ref,
                "kind": h.kind,
                "date": h.on.isoformat(),
                "title": _clip(h.title, 200),
                "text": _clip(h.text, MAX_TEXT),
                "status": h.status,
            }
            for h in recent
        ],
    }


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

    @classmethod
    def request(cls, *, deal_id: UUID, requested_by: UUID, payload: dict[str, Any]) -> DealBrief:
        validate_language(str(payload.get("language")))
        return cls(
            id=uuid4(),
            deal_id=deal_id,
            requested_by=requested_by,
            payload=payload,
            source_hash=payload_fingerprint(payload),
            status=InsightStatus.PENDING,
            created_at=datetime.now(UTC),
        )

    @property
    def is_finished(self) -> bool:
        return self.status in (InsightStatus.SUCCEEDED, InsightStatus.FAILED)

    def mark_submitted(self, ai_job_id: str) -> None:
        if self.status is not InsightStatus.PENDING or not ai_job_id:
            raise DomainValidationError("Only a pending brief can be submitted.")
        self.ai_job_id = ai_job_id
        self.status = InsightStatus.SUBMITTED
        self.payload = None  # the AI Platform has it now; keep only the fingerprint

    def mark_succeeded(
        self, *, result: dict[str, Any], schema_id: str, provider: str, model: str, is_mock: bool
    ) -> DealBriefCompleted:
        if self.status is not InsightStatus.SUBMITTED:
            raise DomainValidationError("Only a submitted brief can succeed.")
        if schema_id != OUTPUT_SCHEMA:
            raise DomainValidationError("The AI result uses an unsupported output contract.")
        self.result = result
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
        self.finished_at = datetime.now(UTC)
        return self._completed(succeeded=False)

    def _completed(self, *, succeeded: bool) -> DealBriefCompleted:
        return DealBriefCompleted(
            brief_id=self.id,
            deal_id=self.deal_id,
            requested_by=self.requested_by,
            succeeded=succeeded,
        )
