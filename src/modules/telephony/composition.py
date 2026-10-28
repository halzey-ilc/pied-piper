"""Wires the call use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.telephony.application.service import CallService
from modules.telephony.infrastructure.dialer import StubDialer
from modules.telephony.infrastructure.repository import DjangoCallRepository
from modules.telephony.infrastructure.subject_checker import DjangoSubjectChecker
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def call_repository() -> DjangoCallRepository:
    """System-level read access for other modules' workers (no RBAC: callers must
    only use it for work a user was already authorised to start)."""
    return DjangoCallRepository(require_tenant().db_alias)


def call_service() -> CallService:
    alias = require_tenant().db_alias
    return CallService(
        repository=DjangoCallRepository(alias),
        subjects=DjangoSubjectChecker(alias),
        dialer=StubDialer(),
        uow=DjangoUnitOfWork(alias),
        outbox=DjangoEventOutbox(alias),
        audit=DjangoAuditLog(alias),
    )
