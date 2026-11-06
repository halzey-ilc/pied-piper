"""Wires the conversation use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from django.conf import settings

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.communications.application.ports import ChannelSender
from modules.communications.application.service import ConversationService
from modules.communications.infrastructure.contact_linking import LeadContactLinker
from modules.communications.infrastructure.repository import DjangoConversationRepository
from modules.communications.infrastructure.sender import StubSender, WhatsAppCloudSender
from modules.leads.composition import lead_service
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def conversation_repository() -> DjangoConversationRepository:
    """System-level read access for other modules' workers (no RBAC: callers must
    only use it for work a user was already authorised to start)."""
    return DjangoConversationRepository(require_tenant().db_alias)


def _sender() -> ChannelSender:
    whatsapp = settings.INTEGRATIONS["WHATSAPP"]
    token, phone_number_id = whatsapp["ACCESS_TOKEN"], whatsapp["PHONE_NUMBER_ID"]
    if token is None or phone_number_id is None:
        return StubSender()
    return WhatsAppCloudSender(
        base_url=whatsapp["API_BASE_URL"],
        phone_number_id=phone_number_id,
        access_token=token.get_secret_value(),
        fallback=StubSender(),
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
