"""Contracts for asynchronous reports and exports.

Flow (planned): API validates a ReportRequest and stores a job (202 + job id)
-> ``reports`` queue renders from read models/snapshots -> ExportStorage keeps
the file -> client polls the job and downloads via a short-lived link.
Heavy reports never run inside an API request.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReportFormat(StrEnum):
    CSV = "csv"
    XLSX = "xlsx"


class ReportJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"


class ReportRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    report_type: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    period_start: date
    period_end: date
    format: ReportFormat = ReportFormat.CSV
    idempotency_key: str = Field(min_length=8, max_length=128)

    @model_validator(mode="after")
    def _check_period(self) -> ReportRequest:
        if self.period_end < self.period_start:
            raise ValueError("period_end must not be before period_start")
        if (self.period_end - self.period_start).days > 366:
            raise ValueError("report period must not exceed one year")
        return self


class ReportJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: UUID
    requested_by: UUID
    request: ReportRequest
    status: ReportJobStatus
    created_at: datetime
    finished_at: datetime | None = None
    file_key: str | None = None  # key in ExportStorage, never a public URL


class ReportJobRepository(Protocol):
    def create_or_get(self, requested_by: UUID, request: ReportRequest) -> ReportJob:
        """Idempotent on (requested_by, idempotency_key)."""
        ...

    def set_status(
        self, job_id: UUID, status: ReportJobStatus, file_key: str | None = None
    ) -> None: ...


class ReportRenderer(Protocol):
    def render(self, request: ReportRequest) -> bytes: ...


class ExportStorage(Protocol):
    def put(self, key: str, content: bytes, content_type: str) -> None: ...

    def signed_url(self, key: str, ttl_seconds: int) -> str: ...
