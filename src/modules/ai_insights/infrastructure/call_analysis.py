"""Adapters of the call analysis: its rows, the company checklist and the recording."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db.models import F, QuerySet

from modules.access_control.domain.rbac import Principal
from modules.ai_insights.domain.call_analysis import AnalysisStatus, CallAnalysis
from modules.ai_insights.infrastructure.models import CallAnalysisRecord, CallChecklistRecord
from modules.telephony.composition import call_recording, call_service, connection_settings
from shared.domain.errors import ConflictError

_SINGLETON = 1


def _to_domain(row: CallAnalysisRecord) -> CallAnalysis:
    return CallAnalysis(
        id=row.id,
        call_id=row.call_id,
        language=row.language,
        channels=row.channels,
        status=AnalysisStatus(row.status),
        created_at=row.created_at,
        requested_by=row.requested_by,
        transcribe_job_id=row.transcribe_job_id,
        review_job_id=row.review_job_id,
        segments=row.segments,
        duration_s=row.duration_s,
        criteria=row.criteria,
        review=row.review,
        score=row.score,
        is_mock=row.is_mock,
        model=row.model,
        error_code=row.error_code,
        finished_at=row.finished_at,
        version=row.version,
    )


class DjangoAnalysisRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def _qs(self) -> QuerySet[CallAnalysisRecord]:
        return CallAnalysisRecord.objects.using(self.using)

    def add(self, analysis: CallAnalysis) -> None:
        self._qs().create(
            id=analysis.id,
            call_id=analysis.call_id,
            created_at=analysis.created_at,
            requested_by=analysis.requested_by,
            **self._fields(analysis),
        )

    def get(self, analysis_id: UUID) -> CallAnalysis | None:
        row = self._qs().filter(id=analysis_id).first()
        return None if row is None else _to_domain(row)

    def latest_for(self, call_id: UUID) -> CallAnalysis | None:
        row = self._qs().filter(call_id=call_id).order_by("-created_at").first()
        return None if row is None else _to_domain(row)

    def save(self, analysis: CallAnalysis, expected_version: int) -> None:
        updated = (
            self._qs()
            .filter(id=analysis.id, version=expected_version)
            .update(version=F("version") + 1, **self._fields(analysis))
        )
        if updated != 1:
            raise ConflictError("The call analysis was modified concurrently; retry.")
        analysis.version = expected_version + 1

    def _fields(self, a: CallAnalysis) -> dict[str, Any]:
        return {
            "language": a.language,
            "channels": a.channels,
            "status": a.status.value,
            "transcribe_job_id": a.transcribe_job_id,
            "review_job_id": a.review_job_id,
            "segments": a.segments,
            "duration_s": a.duration_s,
            "criteria": a.criteria,
            "review": a.review,
            "score": a.score,
            "is_mock": a.is_mock,
            "model": a.model,
            "error_code": a.error_code,
            "finished_at": a.finished_at,
        }


class DjangoChecklistStore:
    def __init__(self, using: str) -> None:
        self.using = using

    def get(self) -> list[dict[str, str]] | None:
        row = CallChecklistRecord.objects.using(self.using).filter(id=_SINGLETON).first()
        return list(row.criteria) if row and row.criteria else None

    def save(self, criteria: list[dict[str, str]]) -> None:
        CallChecklistRecord.objects.using(self.using).update_or_create(
            id=_SINGLETON, defaults={"criteria": criteria}
        )


class TelephonyCallAudio:
    """Recordings and call visibility through the telephony module's public API."""

    def recording(self, call_id: UUID) -> tuple[bytes, str]:
        return call_recording(call_id)

    def defaults(self) -> tuple[str, str]:
        connection = connection_settings()
        if connection is None:
            return "ru", "mono"
        return connection.call_language, connection.record_channels

    def check_visible(self, principal: Principal, call_id: UUID) -> None:
        call_service().get(principal, call_id)
