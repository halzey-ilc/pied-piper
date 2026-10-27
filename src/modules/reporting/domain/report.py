"""Report job domain. Pure Python: no Django, no ORM."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from shared.domain.errors import ConflictError, DomainValidationError

MAX_RANGE_DAYS = 366
_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


class ReportType(StrEnum):
    KPI_SUMMARY = "kpi_summary"


class ReportFormat(StrEnum):
    CSV = "csv"


class ReportStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


_TERMINAL = (ReportStatus.SUCCEEDED, ReportStatus.FAILED)


class ReportStateError(ConflictError):
    code = "report_invalid_state"


@dataclass(frozen=True, slots=True)
class ReportRequest:
    report_type: ReportType
    period_start: date
    period_end: date
    fmt: ReportFormat
    idempotency_key: str

    def __post_init__(self) -> None:
        if self.period_end < self.period_start:
            raise DomainValidationError("period_end must not be before period_start.")
        if (self.period_end - self.period_start).days > MAX_RANGE_DAYS:
            raise DomainValidationError("The report period must not exceed one year.")
        if not _KEY_RE.match(self.idempotency_key):
            raise DomainValidationError("idempotency_key must be 8-128 safe characters.")


@dataclass(slots=True)
class ReportJob:
    id: UUID
    requested_by: UUID
    request: ReportRequest
    status: ReportStatus
    created_at: datetime
    finished_at: datetime | None = None
    file_key: str | None = None
    error: str | None = None

    def mark_running(self) -> None:
        if self.status is not ReportStatus.QUEUED:
            raise ReportStateError("Only a queued report can start running.")
        self.status = ReportStatus.RUNNING

    def mark_succeeded(self, file_key: str, finished_at: datetime) -> None:
        self.status = ReportStatus.SUCCEEDED
        self.file_key = file_key
        self.finished_at = finished_at

    def mark_failed(self, error: str, finished_at: datetime) -> None:
        self.status = ReportStatus.FAILED
        self.error = error[:300]
        self.finished_at = finished_at

    @property
    def is_downloadable(self) -> bool:
        return self.status is ReportStatus.SUCCEEDED and self.file_key is not None
