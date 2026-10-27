from __future__ import annotations

from typing import Protocol
from uuid import UUID

from modules.reporting.domain.report import ReportJob, ReportRequest, ReportStatus


class ReportJobRepository(Protocol):
    def create_or_get(self, requested_by: UUID, request: ReportRequest) -> tuple[ReportJob, bool]:
        """Idempotent on (requested_by, idempotency_key). Returns (job, created)."""
        ...

    def get(self, job_id: UUID) -> ReportJob | None: ...

    def set_status(
        self,
        job_id: UUID,
        status: ReportStatus,
        *,
        file_key: str | None = None,
        error: str | None = None,
    ) -> None: ...


class ReportRenderer(Protocol):
    def render(self, request: ReportRequest) -> tuple[bytes, str]:
        """Return (content, content_type) for the requested report."""
        ...


class ExportStorage(Protocol):
    def put(self, key: str, content: bytes, content_type: str) -> None: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...


class ReportScheduler(Protocol):
    def schedule(self, *, tenant_slug: str, job_id: UUID) -> None:
        """Queue rendering after the current transaction commits."""
        ...
