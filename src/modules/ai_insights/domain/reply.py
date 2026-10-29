"""The ReplySuggestion aggregate: an AI classification of a customer's latest
message and a reply draft (AI Communication Assistant).

The draft is a suggestion for a person. Nothing here sends a message: the user
reviews, edits and sends it through the conversation's own send endpoint.

The job input contains message texts, so it is rebuilt from the conversation
when submitted and never stored; only its fingerprint is kept, and a
conversation that changed in between is not analysed (``source_changed``).

pending -> submitted -> succeeded | failed
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import UUID, uuid4

from modules.ai_insights.domain.insight import InsightStatus
from modules.ai_insights.domain.report import payload_fingerprint, validate_language
from shared.domain.errors import DomainValidationError
from shared.domain.events import DomainEvent

JOB_TYPE = "message.reply.v1"
OUTPUT_SCHEMA = "message_reply.output.v1"
MAX_MESSAGES = 20
MAX_TEXT = 4096


@dataclass(frozen=True, slots=True)
class MessageSnapshot:
    id: UUID
    direction: str  # inbound (customer) | outbound (company)
    text: str


def build_payload(
    *, language: str, channel: str, messages: list[MessageSnapshot]
) -> dict[str, Any]:
    """The job input: the latest messages, oldest first, ending with the customer.

    Contact identifiers and names are left out on purpose.
    """
    recent = messages[-MAX_MESSAGES:]
    if not recent or recent[-1].direction != "inbound":
        raise DomainValidationError(
            "Nothing to answer: the last message in the conversation is not from the customer."
        )
    return {
        "language": validate_language(language),
        "channel": channel,
        "messages": [
            {"ref": str(m.id), "direction": m.direction, "text": m.text[:MAX_TEXT]}
            for m in recent
            if m.text.strip()
        ],
    }


@dataclass(frozen=True, kw_only=True)
class ReplySuggestionCompleted(DomainEvent):
    event_type: ClassVar[str] = "ai_insights.reply_suggestion_completed"

    suggestion_id: UUID
    conversation_id: UUID
    succeeded: bool

    def payload(self) -> dict[str, Any]:
        return {
            "suggestion_id": str(self.suggestion_id),
            "conversation_id": str(self.conversation_id),
            "succeeded": self.succeeded,
        }


@dataclass
class ReplySuggestion:
    id: UUID
    conversation_id: UUID
    requested_by: UUID
    language: str
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
    def request(
        cls, *, conversation_id: UUID, requested_by: UUID, payload: dict[str, Any]
    ) -> ReplySuggestion:
        return cls(
            id=uuid4(),
            conversation_id=conversation_id,
            requested_by=requested_by,
            language=validate_language(str(payload.get("language"))),
            source_hash=payload_fingerprint(payload),
            status=InsightStatus.PENDING,
            created_at=datetime.now(UTC),
        )

    @property
    def is_finished(self) -> bool:
        return self.status in (InsightStatus.SUCCEEDED, InsightStatus.FAILED)

    def mark_submitted(self, ai_job_id: str) -> None:
        if self.status is not InsightStatus.PENDING or not ai_job_id:
            raise DomainValidationError("Only a pending suggestion can be submitted.")
        self.ai_job_id = ai_job_id
        self.status = InsightStatus.SUBMITTED

    def mark_succeeded(
        self, *, result: dict[str, Any], schema_id: str, provider: str, model: str, is_mock: bool
    ) -> ReplySuggestionCompleted:
        if self.status is not InsightStatus.SUBMITTED:
            raise DomainValidationError("Only a submitted suggestion can succeed.")
        if schema_id != OUTPUT_SCHEMA:
            raise DomainValidationError("The AI result uses an unsupported output contract.")
        self.result = result
        self.provider = provider
        self.model = model
        self.is_mock = is_mock
        self.status = InsightStatus.SUCCEEDED
        self.finished_at = datetime.now(UTC)
        return self._completed(succeeded=True)

    def mark_failed(self, error_code: str) -> ReplySuggestionCompleted:
        if self.is_finished:
            raise DomainValidationError("The suggestion is already finished.")
        self.error_code = error_code[:64]
        self.status = InsightStatus.FAILED
        self.finished_at = datetime.now(UTC)
        return self._completed(succeeded=False)

    def _completed(self, *, succeeded: bool) -> ReplySuggestionCompleted:
        return ReplySuggestionCompleted(
            suggestion_id=self.id, conversation_id=self.conversation_id, succeeded=succeeded
        )
