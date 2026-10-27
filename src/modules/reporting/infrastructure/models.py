from __future__ import annotations

import uuid

from django.db import models

from modules.reporting.domain.report import (
    ReportFormat,
    ReportStatus,
    ReportType,
)


class ReportJobRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requested_by = models.UUIDField()
    report_type = models.CharField(
        max_length=32, choices=[(t.value, t.name.title()) for t in ReportType]
    )
    period_start = models.DateField()
    period_end = models.DateField()
    fmt = models.CharField(max_length=8, choices=[(f.value, f.name) for f in ReportFormat])
    idempotency_key = models.CharField(max_length=128)
    status = models.CharField(
        max_length=10,
        choices=[(s.value, s.name.title()) for s in ReportStatus],
        default=ReportStatus.QUEUED.value,
    )
    file_key = models.CharField(max_length=255, null=True, blank=True)  # noqa: DJ001
    error = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "reporting_report_job"
        constraints = [
            models.UniqueConstraint(
                fields=["requested_by", "idempotency_key"], name="report_job_idempotency"
            ),
        ]
        indexes = [
            models.Index(fields=["status", "created_at"], name="report_job_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.report_type}:{self.status}"
