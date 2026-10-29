"""The AiReport aggregate: an AI explanation of numbers the CRM computed itself.

Two kinds share one lifecycle:

* ``operations_digest`` - the findings of the operations rules (refs like
  ``"lead:<uuid>"``, kinds, ages, thresholds);
* ``kpi_explanation``   - KPI values for a period and the one before, with
  changes precomputed here (the model never calculates; principle 2, ADR-005).

The input is a snapshot taken at request time and contains identifiers and
numbers only: no names, amounts of individual deals or free text.
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

LANGUAGES = ("ru", "ky", "en")
MAX_FINDINGS_SENT = 200


class ReportKind(StrEnum):
    OPERATIONS_DIGEST = "operations_digest"
    KPI_EXPLANATION = "kpi_explanation"
    RISK_REVIEW = "risk_review"


class ReportTrigger(StrEnum):
    MANUAL = "manual"
    DAILY = "daily"


JOB_TYPES: dict[ReportKind, str] = {
    ReportKind.OPERATIONS_DIGEST: "operations.digest.v1",
    ReportKind.KPI_EXPLANATION: "kpi.explanation.v1",
    ReportKind.RISK_REVIEW: "risk.review.v1",
}
OUTPUT_SCHEMAS: dict[ReportKind, str] = {
    ReportKind.OPERATIONS_DIGEST: "operations_digest.output.v1",
    ReportKind.KPI_EXPLANATION: "kpi_explanation.output.v1",
    ReportKind.RISK_REVIEW: "risk_review.output.v1",
}


def finding_ref(record_type: str, record_id: UUID) -> str:
    return f"{record_type}:{record_id}"


def payload_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_language(language: str) -> str:
    if language not in LANGUAGES:
        raise DomainValidationError(f"language must be one of {', '.join(LANGUAGES)}.")
    return language


@dataclass(frozen=True, kw_only=True)
class AiReportCompleted(DomainEvent):
    event_type: ClassVar[str] = "ai_insights.report_completed"

    report_id: UUID
    kind: str
    user_id: UUID
    succeeded: bool

    def payload(self) -> dict[str, Any]:
        return {
            "report_id": str(self.report_id),
            "kind": self.kind,
            "user_id": str(self.user_id),
            "succeeded": self.succeeded,
        }


@dataclass
class AiReport:
    id: UUID
    kind: ReportKind
    requested_by: UUID
    trigger: ReportTrigger
    report_date: date
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
        kind: ReportKind,
        requested_by: UUID,
        trigger: ReportTrigger,
        payload: dict[str, Any],
    ) -> AiReport:
        validate_language(str(payload.get("language")))
        now = datetime.now(UTC)
        return cls(
            id=uuid4(),
            kind=kind,
            requested_by=requested_by,
            trigger=trigger,
            report_date=now.date(),
            payload=payload,
            payload_hash=payload_fingerprint(payload),
            status=InsightStatus.PENDING,
            created_at=now,
        )

    @property
    def job_type(self) -> str:
        return JOB_TYPES[self.kind]

    @property
    def is_finished(self) -> bool:
        return self.status in (InsightStatus.SUCCEEDED, InsightStatus.FAILED)

    def mark_submitted(self, ai_job_id: str) -> None:
        if self.status is not InsightStatus.PENDING or not ai_job_id:
            raise DomainValidationError("Only a pending report can be submitted.")
        self.ai_job_id = ai_job_id
        self.status = InsightStatus.SUBMITTED

    def mark_succeeded(
        self, *, result: dict[str, Any], schema_id: str, provider: str, model: str, is_mock: bool
    ) -> AiReportCompleted:
        if self.status is not InsightStatus.SUBMITTED:
            raise DomainValidationError("Only a submitted report can succeed.")
        if schema_id != OUTPUT_SCHEMAS[self.kind]:
            raise DomainValidationError("The AI result uses an unsupported output contract.")
        self.result = result
        self.provider = provider
        self.model = model
        self.is_mock = is_mock
        self.status = InsightStatus.SUCCEEDED
        self.finished_at = datetime.now(UTC)
        return self._completed(succeeded=True)

    def mark_failed(self, error_code: str) -> AiReportCompleted:
        if self.is_finished:
            raise DomainValidationError("The report is already finished.")
        self.error_code = error_code[:64]
        self.status = InsightStatus.FAILED
        self.finished_at = datetime.now(UTC)
        return self._completed(succeeded=False)

    def _completed(self, *, succeeded: bool) -> AiReportCompleted:
        return AiReportCompleted(
            report_id=self.id, kind=self.kind.value, user_id=self.requested_by, succeeded=succeeded
        )
