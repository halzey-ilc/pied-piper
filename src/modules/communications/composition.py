"""Wires the conversation use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from django.conf import settings

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.communications.application.ports import ChannelSender
from modules.communications.application.service import ConversationService
from modules.communications.application.templates import TemplateService
from modules.communications.infrastructure.audio import FfmpegVoiceEncoder
from modules.communications.infrastructure.connection import (
    CloudApiConnection,
    GreenApiConnection,
    NoConnection,
)
from modules.communications.infrastructure.contact_linking import LeadContactLinker
from modules.communications.infrastructure.media import HttpMediaFetcher
from modules.communications.infrastructure.repository import DjangoConversationRepository
from modules.communications.infrastructure.sender import (
    GreenApiSender,
    StubSender,
    UnconfiguredSender,
    WhatsAppCloudSender,
)
from modules.communications.infrastructure.templates import DjangoTemplateRepository
from modules.leads.composition import lead_service
from modules.organizations.infrastructure.memberships import DjangoMemberDirectory
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def conversation_repository() -> DjangoConversationRepository:
    """System-level read access for other modules' workers (no RBAC: callers must
    only use it for work a user was already authorised to start)."""
    return DjangoConversationRepository(require_tenant().db_alias)


def whatsapp_connected() -> bool:
    """True only for the company that owns the number, and only when WhatsApp sends for
    real; StubSender and UnconfiguredSender do not. Asks no provider: clients poll this."""
    owner = settings.INTEGRATIONS["WHATSAPP"]["TENANT_SLUG"]
    return owner == require_tenant().slug and isinstance(
        _sender(), WhatsAppCloudSender | GreenApiSender
    )


def _sender() -> ChannelSender:
    fallback = StubSender() if settings.INTEGRATIONS["ALLOW_STUB_SENDER"] else UnconfiguredSender()
    whatsapp = settings.INTEGRATIONS["WHATSAPP"]
    if whatsapp["PROVIDER"] == "green_api":
        green = whatsapp["GREEN_API"]
        if not (green["URL"] and green["INSTANCE_ID"] and green["TOKEN"]):
            return fallback
        return GreenApiSender(
            base_url=green["URL"],
            instance_id=green["INSTANCE_ID"],
            token=green["TOKEN"].get_secret_value(),
            fallback=fallback,
        )
    token, phone_number_id = whatsapp["ACCESS_TOKEN"], whatsapp["PHONE_NUMBER_ID"]
    if token is None or phone_number_id is None:
        return fallback
    return WhatsAppCloudSender(
        base_url=whatsapp["API_BASE_URL"],
        phone_number_id=phone_number_id,
        access_token=token.get_secret_value(),
        fallback=fallback,
    )


def whatsapp_connection() -> GreenApiConnection | CloudApiConnection | NoConnection:
    """The number's connection, for the company that owns it. Settings are global,
    so any other company must not see its state or scan its QR code."""
    whatsapp = settings.INTEGRATIONS["WHATSAPP"]
    provider = whatsapp["PROVIDER"]
    if whatsapp["TENANT_SLUG"] != require_tenant().slug:
        return NoConnection(provider)
    if provider == "green_api":
        green = whatsapp["GREEN_API"]
        if not (green["URL"] and green["INSTANCE_ID"] and green["TOKEN"]):
            return NoConnection(provider)
        return GreenApiConnection(
            base_url=green["URL"],
            instance_id=green["INSTANCE_ID"],
            token=green["TOKEN"].get_secret_value(),
        )
    return CloudApiConnection(
        configured=bool(whatsapp["ACCESS_TOKEN"] and whatsapp["PHONE_NUMBER_ID"])
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
        media=HttpMediaFetcher(),
        members=DjangoMemberDirectory(require_tenant().tenant_id),
        voice=FfmpegVoiceEncoder(),
    )
