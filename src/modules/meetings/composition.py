"""Wires the meeting use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.meetings.application.service import MeetingService
from modules.meetings.infrastructure.repository import DjangoMeetingRepository
from modules.meetings.infrastructure.subject_checker import DjangoSubjectChecker
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def meeting_service() -> MeetingService:
    alias = require_tenant().db_alias
    return MeetingService(
        repository=DjangoMeetingRepository(alias),
        subjects=DjangoSubjectChecker(alias),
        uow=DjangoUnitOfWork(alias),
        outbox=DjangoEventOutbox(alias),
        audit=DjangoAuditLog(alias),
    )
