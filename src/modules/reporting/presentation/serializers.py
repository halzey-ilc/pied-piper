from __future__ import annotations

from typing import Any

from rest_framework import serializers

from modules.reporting.domain.report import ReportFormat, ReportJob, ReportType


class ReportRequestSerializer(serializers.Serializer[dict[str, Any]]):
    report_type = serializers.ChoiceField(
        choices=[t.value for t in ReportType], default=ReportType.KPI_SUMMARY.value
    )
    period_start = serializers.DateField()
    period_end = serializers.DateField()
    fmt = serializers.ChoiceField(
        choices=[f.value for f in ReportFormat], default=ReportFormat.CSV.value
    )
    idempotency_key = serializers.CharField(min_length=8, max_length=128)


class ReportJobSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    report_type = serializers.CharField(read_only=True)
    period_start = serializers.DateField(read_only=True)
    period_end = serializers.DateField(read_only=True)
    fmt = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    error = serializers.CharField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    finished_at = serializers.DateTimeField(read_only=True, allow_null=True)


def report_job_to_dict(job: ReportJob) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "report_type": job.request.report_type.value,
        "period_start": job.request.period_start.isoformat(),
        "period_end": job.request.period_end.isoformat(),
        "fmt": job.request.fmt.value,
        "status": job.status.value,
        "error": job.error,
        "created_at": job.created_at.isoformat(),
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "download_url": f"/api/v1/analytics/reports/{job.id}/download/"
        if job.is_downloadable
        else None,
    }
