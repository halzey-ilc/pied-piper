"""The OperationsDigest aggregate: an AI explanation of one user's operations findings.

The input is a snapshot taken when the digest is requested. It contains
references (``"lead:<uuid>"``), kinds, ages and thresholds only, so neither the
CRM's stored copy nor the AI Platform ever sees names, amounts or free text.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID, uuid4

from modules.ai_insights.domain.insight import InsightStatus
from shared.domain.errors import DomainValidationError
from shared.domain.events import DomainEvent

JOB_TYPE = "operations.digest.v1"
OUTPUT_SCHEMA = "operations_digest.output.v1"
LANGUAGES = ("ru", "ky", "en")
MAX_FINDINGS_SENT = 200


class DigestTrigger(StrEnum):
    MANUAL = "manual"
    DAILY = "daily"


def finding_ref(record_type: str, record_id: UUID) -> str:
    return f"{record_type}:{record_id}"


def payload_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, kw_only=True)
class OperationsDigestCompleted(DomainEvent):
    event_type: ClassVar[str] = "ai_insights.operations_digest_completed"

    digest_id: UUID
    user_id: UUID
    succeeded: bool

    def payload(self) -> dict[str, Any]:
        return {
            "digest_id": str(self.digest_id),
            "user_id": str(self.user_id),
            "succeeded": self.succeeded,
        }


@dataclass
class OperationsDigest:
    id: UUID
    requested_by: UUID
    trigger: DigestTrigger
    digest_date: date
    payload: dict[str, Any]
    payload_hash: str
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
        cls,
        *,
        requested_by: UUID,
        trigger: DigestTrigger,
        language: str,
        thresholds: dict[str, int],
        counts: dict[str, int],
        findings: list[dict[str, Any]],
    ) -> OperationsDigest:
        if language not in LANGUAGES:
            raise DomainValidationError(f"language must be one of {', '.join(LANGUAGES)}.")
        payload = {
            "language": language,
            "thresholds": thresholds,
            "counts": counts,
            "findings": findings[:MAX_FINDINGS_SENT],
        }
        now = datetime.now(UTC)
        return cls(
            id=uuid4(),
            requested_by=requested_by,
            trigger=trigger,
            digest_date=now.date(),
            payload=payload,
            payload_hash=payload_fingerprint(payload),
            status=InsightStatus.PENDING,
            created_at=now,
        )

    @property
    def is_finished(self) -> bool:
        return self.status in (InsightStatus.SUCCEEDED, InsightStatus.FAILED)

    def mark_submitted(self, ai_job_id: str) -> None:
        if self.status is not InsightStatus.PENDING or not ai_job_id:
            raise DomainValidationError("Only a pending digest can be submitted.")
        self.ai_job_id = ai_job_id
        self.status = InsightStatus.SUBMITTED

    def mark_succeeded(
        self, *, result: dict[str, Any], schema_id: str, provider: str, model: str, is_mock: bool
    ) -> OperationsDigestCompleted:
        if self.status is not InsightStatus.SUBMITTED:
            raise DomainValidationError("Only a submitted digest can succeed.")
        if schema_id != OUTPUT_SCHEMA:
            raise DomainValidationError("The AI result uses an unsupported output contract.")
        self.result = result
        self.provider = provider
        self.model = model
        self.is_mock = is_mock
        self.status = InsightStatus.SUCCEEDED
        self.finished_at = datetime.now(UTC)
        return self._completed(succeeded=True)

    def mark_failed(self, error_code: str) -> OperationsDigestCompleted:
        if self.is_finished:
            raise DomainValidationError("The digest is already finished.")
        self.error_code = error_code[:64]
        self.status = InsightStatus.FAILED
        self.finished_at = datetime.now(UTC)
        return self._completed(succeeded=False)

    def _completed(self, *, succeeded: bool) -> OperationsDigestCompleted:
        return OperationsDigestCompleted(
            digest_id=self.id, user_id=self.requested_by, succeeded=succeeded
        )
