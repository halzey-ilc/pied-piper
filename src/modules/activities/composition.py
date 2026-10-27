"""Wires the activity use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from modules.activities.application.service import ActivityService
from modules.activities.infrastructure.repository import DjangoActivityRepository
from modules.activities.infrastructure.subject_checker import DjangoSubjectChecker
from modules.audit.infrastructure.audit_log import DjangoAuditLog
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def activity_service() -> ActivityService:
    alias = require_tenant().db_alias
    return ActivityService(
        repository=DjangoActivityRepository(alias),
        subjects=DjangoSubjectChecker(alias),
        uow=DjangoUnitOfWork(alias),
        outbox=DjangoEventOutbox(alias),
        audit=DjangoAuditLog(alias),
    )
