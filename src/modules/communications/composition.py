"""Wires the conversation use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from django.conf import settings

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.communications.application.ports import ChannelSender
from modules.communications.application.service import ConversationService
from modules.communications.application.templates import TemplateService
from modules.communications.infrastructure.contact_linking import LeadContactLinker
from modules.communications.infrastructure.repository import DjangoConversationRepository
from modules.communications.infrastructure.sender import (
    StubSender,
    UnconfiguredSender,
    WhatsAppCloudSender,
)
from modules.communications.infrastructure.templates import DjangoTemplateRepository
from modules.leads.composition import lead_service
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def conversation_repository() -> DjangoConversationRepository:
    """System-level read access for other modules' workers (no RBAC: callers must
    only use it for work a user was already authorised to start)."""
    return DjangoConversationRepository(require_tenant().db_alias)


def whatsapp_connected() -> bool:
    """True only when WhatsApp sends for real; StubSender and UnconfiguredSender do not."""
    return isinstance(_sender(), WhatsAppCloudSender)


def _sender() -> ChannelSender:
    fallback = StubSender() if settings.INTEGRATIONS["ALLOW_STUB_SENDER"] else UnconfiguredSender()
    whatsapp = settings.INTEGRATIONS["WHATSAPP"]
    token, phone_number_id = whatsapp["ACCESS_TOKEN"], whatsapp["PHONE_NUMBER_ID"]
    if token is None or phone_number_id is None:
        return fallback
    return WhatsAppCloudSender(
        base_url=whatsapp["API_BASE_URL"],
        phone_number_id=phone_number_id,
        access_token=token.get_secret_value(),
        fallback=fallback,
    )


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
        linker=LeadContactLinker(
            lead_service(), settings.INTEGRATIONS["WHATSAPP"]["LEAD_OWNER_USER_ID"]
        ),
    )
