"""Wires the conversation use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.communications.application.ports import ChannelSender
from modules.communications.application.service import ConversationService
from modules.communications.application.templates import TemplateService
from modules.communications.infrastructure.repository import DjangoConversationRepository
from modules.communications.infrastructure.sender import StubSender
from modules.communications.infrastructure.templates import DjangoTemplateRepository
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def conversation_repository() -> DjangoConversationRepository:
    """System-level read access for other modules' workers (no RBAC: callers must
    only use it for work a user was already authorised to start)."""
    return DjangoConversationRepository(require_tenant().db_alias)


def outbound_connected() -> bool:
    """True only when a real provider sender is wired; the scaffold's StubSender is not."""
    return not isinstance(_sender(), StubSender)


def _sender() -> ChannelSender:
    # No provider adapter is wired in this installation (see infrastructure/sender.py).
    return StubSender()


def template_service() -> TemplateService:
    alias = require_tenant().db_alias
    return TemplateService(
        repository=DjangoTemplateRepository(alias),
        uow=DjangoUnitOfWork(alias),
        audit=DjangoAuditLog(alias),
    )


def conversation_service() -> ConversationService:
    alias = require_tenant().db_alias
    return ConversationService(
        repository=DjangoConversationRepository(alias),
        sender=_sender(),
        uow=DjangoUnitOfWork(alias),
        outbox=DjangoEventOutbox(alias),
        audit=DjangoAuditLog(alias),
    )
