"""The CallInsight aggregate: one AI analysis of one call, and its lifecycle.

pending -> submitted -> succeeded
   |           \\------> failed
   +-------------------> failed
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID, uuid4

from shared.domain.errors import DomainValidationError
from shared.domain.events import DomainEvent


class InsightKind(StrEnum):
    CALL_SUMMARY = "call_summary"


class InsightStatus(StrEnum):
    PENDING = "pending"  # stored, not yet accepted by the AI Platform
    SUBMITTED = "submitted"  # accepted; the AI Platform is working on it
    SUCCEEDED = "succeeded"
    FAILED = "failed"


# The AI Platform job type and the only output contract this version accepts.
JOB_TYPES: dict[InsightKind, str] = {InsightKind.CALL_SUMMARY: "call.summary.v1"}
OUTPUT_SCHEMAS: dict[InsightKind, str] = {InsightKind.CALL_SUMMARY: "call_summary.output.v1"}


def source_fingerprint(text: str) -> str:
    """Identifies the analysed text so an unchanged call is not analysed twice."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, kw_only=True)
class CallInsightCompleted(DomainEvent):
    event_type: ClassVar[str] = "ai_insights.call_insight_completed"

    insight_id: UUID
    call_id: UUID
    kind: str
    requested_by: UUID
    succeeded: bool

    def payload(self) -> dict[str, Any]:
        return {
            "insight_id": str(self.insight_id),
            "call_id": str(self.call_id),
            "kind": self.kind,
            "requested_by": str(self.requested_by),
            "succeeded": self.succeeded,
        }


@dataclass
class CallInsight:
    id: UUID
    call_id: UUID
    kind: InsightKind
    status: InsightStatus
    requested_by: UUID
    source_hash: str
    created_at: datetime
    ai_job_id: str | None = None
    result: dict[str, Any] | None = None
    schema_id: str | None = None
    provider: str | None = None
    model: str | None = None
    is_mock: bool = False
    error_code: str | None = None
    finished_at: datetime | None = None
    version: int = 1
    _events: list[DomainEvent] = field(default_factory=list, repr=False, compare=False)

    @classmethod
    def request(
        cls, *, call_id: UUID, kind: InsightKind, requested_by: UUID, source_text: str
    ) -> CallInsight:
        if not source_text.strip():
            raise DomainValidationError("The call has no text to analyse.")
        return cls(
            id=uuid4(),
            call_id=call_id,
            kind=kind,
            status=InsightStatus.PENDING,
            requested_by=requested_by,
            source_hash=source_fingerprint(source_text),
            created_at=datetime.now(UTC),
        )

    @property
    def job_type(self) -> str:
        return JOB_TYPES[self.kind]

    @property
    def is_finished(self) -> bool:
        return self.status in (InsightStatus.SUCCEEDED, InsightStatus.FAILED)

    def mark_submitted(self, ai_job_id: str) -> None:
        if self.status is not InsightStatus.PENDING:
            raise DomainValidationError("Only a pending insight can be submitted.")
        if not ai_job_id:
            raise DomainValidationError("The AI Platform returned no job id.")
        self.ai_job_id = ai_job_id
        self.status = InsightStatus.SUBMITTED

    def mark_succeeded(
        self, *, result: dict[str, Any], schema_id: str, provider: str, model: str, is_mock: bool
    ) -> CallInsightCompleted:
        if self.status is not InsightStatus.SUBMITTED:
            raise DomainValidationError("Only a submitted insight can succeed.")
        if schema_id != OUTPUT_SCHEMAS[self.kind]:
            raise DomainValidationError("The AI result uses an unsupported output contract.")
        self.result = result
        self.schema_id = schema_id
        self.provider = provider
        self.model = model
        self.is_mock = is_mock
        self.status = InsightStatus.SUCCEEDED
        self.finished_at = datetime.now(UTC)
        return self._completed(succeeded=True)

    def mark_failed(self, error_code: str) -> CallInsightCompleted:
        if self.is_finished:
            raise DomainValidationError("The insight is already finished.")
        self.error_code = error_code[:64]
        self.status = InsightStatus.FAILED
        self.finished_at = datetime.now(UTC)
        return self._completed(succeeded=False)

    def _completed(self, *, succeeded: bool) -> CallInsightCompleted:
        return CallInsightCompleted(
            insight_id=self.id,
            call_id=self.call_id,
            kind=self.kind.value,
            requested_by=self.requested_by,
            succeeded=succeeded,
        )
