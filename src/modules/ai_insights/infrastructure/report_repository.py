from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from django.db.models import F, QuerySet

from modules.ai_insights.domain.insight import InsightStatus
from modules.ai_insights.domain.report import AiReport, ReportKind, ReportTrigger
from modules.ai_insights.infrastructure.models import AiReportRecord
from shared.domain.errors import ConflictError


def _to_domain(row: AiReportRecord) -> AiReport:
    return AiReport(
        id=row.id,
        kind=ReportKind(row.kind),
        requested_by=row.requested_by,
        trigger=ReportTrigger(row.trigger),
        report_date=row.report_date,
        payload=row.payload,
        payload_hash=row.payload_hash,
        status=InsightStatus(row.status),
        created_at=row.created_at,
        ai_job_id=row.ai_job_id,
        result=row.result,
        provider=row.provider,
        model=row.model,
        is_mock=row.is_mock,
        error_code=row.error_code,
        finished_at=row.finished_at,
        version=row.version,
    )


class DjangoReportRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def _qs(self) -> QuerySet[AiReportRecord]:
        return AiReportRecord.objects.using(self.using)

    def add(self, report: AiReport) -> None:
        self._qs().create(
            id=report.id,
            kind=report.kind.value,
            requested_by=report.requested_by,
            trigger=report.trigger.value,
            report_date=report.report_date,
            payload=report.payload,
            payload_hash=report.payload_hash,
            created_at=report.created_at,
            **self._fields(report),
        )

    def get(self, report_id: UUID) -> AiReport | None:
        row = self._qs().filter(id=report_id).first()
        return None if row is None else _to_domain(row)

    def latest_for_user(self, user_id: UUID, kind: ReportKind) -> AiReport | None:
        row = (
            self._qs().filter(requested_by=user_id, kind=kind.value).order_by("-created_at").first()
        )
        return None if row is None else _to_domain(row)

    def find_reusable(self, user_id: UUID, kind: ReportKind, payload_hash: str) -> AiReport | None:
        row = (
            self._qs()
            .filter(requested_by=user_id, kind=kind.value, payload_hash=payload_hash)
            .exclude(status=InsightStatus.FAILED.value)
            .order_by("-created_at")
            .first()
        )
        return None if row is None else _to_domain(row)

    def exists_for_day(
        self, user_id: UUID, kind: ReportKind, trigger: ReportTrigger, day: date
    ) -> bool:
        return (
            self._qs()
            .filter(requested_by=user_id, kind=kind.value, trigger=trigger.value, report_date=day)
            .exists()
        )

    def save(self, report: AiReport, expected_version: int) -> None:
        updated = (
            self._qs()
            .filter(id=report.id, version=expected_version)
            .update(version=F("version") + 1, **self._fields(report))
        )
        if updated != 1:
            raise ConflictError("The AI report was modified concurrently; retry.")
        report.version = expected_version + 1

    def _fields(self, report: AiReport) -> dict[str, Any]:
        return {
            "status": report.status.value,
            "ai_job_id": report.ai_job_id,
            "result": report.result,
            "provider": report.provider,
            "model": report.model,
            "is_mock": report.is_mock,
            "error_code": report.error_code,
            "finished_at": report.finished_at,
        }
