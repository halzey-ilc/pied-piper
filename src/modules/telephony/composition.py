"""Wires the call use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from uuid import UUID

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.telephony.application.service import CallService
from modules.telephony.application.sipuni import SipuniService
from modules.telephony.domain.sipuni import SipuniConnection
from modules.telephony.infrastructure.dialer import StubDialer
from modules.telephony.infrastructure.repository import DjangoCallRepository
from modules.telephony.infrastructure.sipuni import (
    AiRecordingSink,
    CustomerMatcher,
    DjangoConnectionStore,
    MemberDepartments,
    SipuniClient,
    SipuniDialer,
)
from modules.telephony.infrastructure.subject_checker import DjangoSubjectChecker
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from shared.domain.errors import NotFoundError
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
        dialer=SipuniDialer(DjangoConnectionStore(alias), StubDialer()),
        uow=DjangoUnitOfWork(alias),
        outbox=DjangoEventOutbox(alias),
        audit=DjangoAuditLog(alias),
    )


def sipuni_service() -> SipuniService:
    tenant = require_tenant()
    alias = tenant.db_alias
    return SipuniService(
        store=DjangoConnectionStore(alias),
        calls=DjangoCallRepository(alias),
        contacts=CustomerMatcher(),
        departments=MemberDepartments(tenant.tenant_id),
        recordings=AiRecordingSink(alias, tenant.slug),
        uow=DjangoUnitOfWork(alias),
        outbox=DjangoEventOutbox(alias),
        audit=DjangoAuditLog(alias),
    )


def call_recording(call_id: UUID) -> tuple[bytes, str]:
    """The audio of a call, from the company's PBX (system path for the AI worker: the
    analysis was started by the call itself or by a user who may see it). Raises
    NotFoundError without a call, a recording or a connected PBX."""
    alias = require_tenant().db_alias
    call = DjangoCallRepository(alias).get(call_id)
    connection = DjangoConnectionStore(alias).get()
    if call is None or not call.recording_url or connection is None:
        raise NotFoundError("No recording for this call.")
    return SipuniClient(connection).recording(call.recording_url)


def connection_settings() -> SipuniConnection | None:
    """Language and recording channels for the AI worker; None without a PBX."""
    return DjangoConnectionStore(require_tenant().db_alias).get()
