from __future__ import annotations

from uuid import UUID

from django.utils import timezone

from modules.reporting.domain.report import (
    ReportFormat,
    ReportJob,
    ReportRequest,
    ReportStatus,
    ReportType,
)
from modules.reporting.infrastructure.models import ReportJobRecord


def _to_domain(row: ReportJobRecord) -> ReportJob:
    return ReportJob(
        id=row.id,
        requested_by=row.requested_by,
        request=ReportRequest(
            report_type=ReportType(row.report_type),
            period_start=row.period_start,
            period_end=row.period_end,
            fmt=ReportFormat(row.fmt),
            idempotency_key=row.idempotency_key,
        ),
        status=ReportStatus(row.status),
        created_at=row.created_at,
        finished_at=row.finished_at,
        file_key=row.file_key,
        error=row.error or None,
    )


class DjangoReportJobRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def create_or_get(self, requested_by: UUID, request: ReportRequest) -> tuple[ReportJob, bool]:
        row, created = ReportJobRecord.objects.using(self.using).get_or_create(
            requested_by=requested_by,
            idempotency_key=request.idempotency_key,
            defaults={
                "report_type": request.report_type.value,
                "period_start": request.period_start,
                "period_end": request.period_end,
                "fmt": request.fmt.value,
                "status": ReportStatus.QUEUED.value,
            },
        )
        return _to_domain(row), created

    def get(self, job_id: UUID) -> ReportJob | None:
        row = ReportJobRecord.objects.using(self.using).filter(id=job_id).first()
        return None if row is None else _to_domain(row)

    def set_status(
        self,
        job_id: UUID,
        status: ReportStatus,
        *,
        file_key: str | None = None,
        error: str | None = None,
    ) -> None:
        fields: dict[str, object] = {"status": status.value}
        if file_key is not None:
            fields["file_key"] = file_key
        if error is not None:
            fields["error"] = error[:300]
        if status in (ReportStatus.SUCCEEDED, ReportStatus.FAILED):
            fields["finished_at"] = timezone.now()
        ReportJobRecord.objects.using(self.using).filter(id=job_id).update(**fields)
