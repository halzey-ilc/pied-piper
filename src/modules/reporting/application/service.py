"""Report use cases: request a report (fast, returns a job) and render it (async)."""

from __future__ import annotations

from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.reporting.application.ports import (
    ExportStorage,
    ReportJobRepository,
    ReportRenderer,
    ReportScheduler,
)
from modules.reporting.domain.report import (
    ReportFormat,
    ReportJob,
    ReportRequest,
    ReportStatus,
)
from shared.application.ports import UnitOfWork
from shared.domain.errors import NotFoundError

AGGREGATE = "report_job"


class ReportService:
    def __init__(
        self,
        jobs: ReportJobRepository,
        renderer: ReportRenderer,
        storage: ExportStorage,
        scheduler: ReportScheduler,
        uow: UnitOfWork,
        audit: AuditLog,
        *,
        tenant_slug: str,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._jobs = jobs
        self._renderer = renderer
        self._storage = storage
        self._scheduler = scheduler
        self._uow = uow
        self._audit = audit
        self._tenant_slug = tenant_slug
        self._policy = policy or AccessPolicy()

    def request_report(
        self, principal: Principal, request: ReportRequest
    ) -> tuple[ReportJob, bool]:
        self._policy.require(principal, Permission.REPORTS_GENERATE)
        with self._uow:
            job, created = self._jobs.create_or_get(principal.user_id, request)
            if created:
                self._audit.record(
                    AuditEntry(
                        actor_user_id=principal.user_id,
                        action="report.requested",
                        resource_type=AGGREGATE,
                        resource_id=str(job.id),
                    )
                )
                self._scheduler.schedule(tenant_slug=self._tenant_slug, job_id=job.id)
        return job, created

    def get_job(self, principal: Principal, job_id: UUID) -> ReportJob:
        # Reports are company-wide; anyone with reports.view may see any job.
        self._policy.require(principal, Permission.REPORTS_VIEW)
        job = self._jobs.get(job_id)
        if job is None:
            raise NotFoundError("Report job not found.")
        return job

    def download(self, principal: Principal, job_id: UUID) -> tuple[bytes, str, str]:
        job = self.get_job(principal, job_id)
        if not job.is_downloadable or job.file_key is None:
            raise NotFoundError("Report is not ready for download.")
        content = self._storage.get(job.file_key)
        filename = f"{job.request.report_type.value}-{job.request.period_start.isoformat()}.csv"
        return content, _content_type(job.request.fmt), filename

    def run(self, job_id: UUID) -> None:
        """Render the report. Called by the worker; never from the request path."""
        job = self._jobs.get(job_id)
        if job is None or job.status in (ReportStatus.SUCCEEDED, ReportStatus.FAILED):
            return
        self._jobs.set_status(job_id, ReportStatus.RUNNING)
        try:
            content, content_type = self._renderer.render(job.request)
            key = f"{self._tenant_slug}/{job_id}.{job.request.fmt.value}"
            self._storage.put(key, content, content_type)
        except Exception as exc:
            self._jobs.set_status(job_id, ReportStatus.FAILED, error=type(exc).__name__)
            raise
        self._jobs.set_status(job_id, ReportStatus.SUCCEEDED, file_key=key)


def _content_type(fmt: ReportFormat) -> str:
    return "text/csv" if fmt is ReportFormat.CSV else "application/octet-stream"
