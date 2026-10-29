"""Wires the conversation use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.communications.application.service import ConversationService
from modules.communications.infrastructure.repository import DjangoConversationRepository
from modules.communications.infrastructure.sender import StubSender
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def conversation_repository() -> DjangoConversationRepository:
    """System-level read access for other modules' workers (no RBAC: callers must
    only use it for work a user was already authorised to start)."""
    return DjangoConversationRepository(require_tenant().db_alias)


def conversation_service() -> ConversationService:
    alias = require_tenant().db_alias
    return ConversationService(
        repository=DjangoConversationRepository(alias),
        sender=StubSender(),
        uow=DjangoUnitOfWork(alias),
        outbox=DjangoEventOutbox(alias),
        audit=DjangoAuditLog(alias),
    )
