from __future__ import annotations

from uuid import UUID

from celery import current_app
from django.db import transaction

from platform_core.application.context import current_correlation_id

PROCESS_REPORT_TASK = "reports.process_report"


class CeleryReportScheduler:
    """Enqueues rendering only after the job row has been committed."""

    def __init__(self, using: str) -> None:
        self.using = using

    def schedule(self, *, tenant_slug: str, job_id: UUID) -> None:
        correlation_id = current_correlation_id() or ""

        def _send() -> None:
            current_app.send_task(
                PROCESS_REPORT_TASK,
                kwargs={"tenant_slug": tenant_slug, "job_id": str(job_id)},
                queue="reports",
                headers={"crm_correlation_id": correlation_id},
            )

        transaction.on_commit(_send, using=self.using)
