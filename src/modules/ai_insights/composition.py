"""Wires the AI insight use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from functools import cache

from django.conf import settings

from modules.ai_insights.application.deal_brief_service import DealBriefService
from modules.ai_insights.application.reply_service import ReplySuggestionService
from modules.ai_insights.application.report_service import AiReportService
from modules.ai_insights.application.service import CallInsightService
from modules.ai_insights.infrastructure.calls import TelephonyCallReader
from modules.ai_insights.infrastructure.client import HttpAiPlatformClient
from modules.ai_insights.infrastructure.conversations import CommunicationsConversationReader
from modules.ai_insights.infrastructure.deal_brief_repository import DjangoDealBriefRepository
from modules.ai_insights.infrastructure.deals import CrmDealContextReader
from modules.ai_insights.infrastructure.reply_repository import DjangoReplyRepository
from modules.ai_insights.infrastructure.report_repository import DjangoReportRepository
from modules.ai_insights.infrastructure.report_sources import (
    OperationsFindingsSource,
    OrganizationMemberDirectory,
    RiskSignalsSource,
    SalesKpiSource,
)
from modules.ai_insights.infrastructure.repository import DjangoInsightRepository
from modules.ai_insights.infrastructure.scheduler import (
    CeleryDealBriefScheduler,
    CeleryInsightScheduler,
    CeleryReplyScheduler,
    CeleryReportScheduler,
)
from modules.audit.infrastructure.audit_log import DjangoAuditLog
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


@cache
def ai_platform_client() -> HttpAiPlatformClient:
    cfg = settings.AI_PLATFORM
    return HttpAiPlatformClient(
        base_url=cfg["URL"] or "http://ai-platform.invalid",
        token_key=cfg["TOKEN_KEY"] or "",
        issuer=cfg["ISSUER"],
        audience=cfg["AUDIENCE"],
        token_ttl_seconds=cfg["TOKEN_TTL_SECONDS"],
        connect_timeout=cfg["CONNECT_TIMEOUT"],
        read_timeout=cfg["READ_TIMEOUT"],
    )


def ai_report_service() -> AiReportService:
    tenant = require_tenant()
    alias = tenant.db_alias
    cfg = settings.AI_PLATFORM
    return AiReportService(
        repository=DjangoReportRepository(alias),
        findings=OperationsFindingsSource(),
        kpis=SalesKpiSource(),
        risks=RiskSignalsSource(),
        members=OrganizationMemberDirectory(),
        client=ai_platform_client(),
        scheduler=CeleryReportScheduler(alias),
        uow=DjangoUnitOfWork(alias),
        outbox=DjangoEventOutbox(alias),
        audit=DjangoAuditLog(alias),
        tenant_slug=tenant.slug,
        tenant_id=tenant.tenant_id,
        enabled=cfg["ENABLED"],
        default_language=cfg["DEFAULT_LANGUAGE"],
        deadline_seconds=cfg["DEADLINE_SECONDS"],
    )


def deal_brief_service() -> DealBriefService:
    tenant = require_tenant()
    alias = tenant.db_alias
    cfg = settings.AI_PLATFORM
    return DealBriefService(
        repository=DjangoDealBriefRepository(alias),
        deals=CrmDealContextReader(),
        client=ai_platform_client(),
        scheduler=CeleryDealBriefScheduler(alias),
        uow=DjangoUnitOfWork(alias),
        outbox=DjangoEventOutbox(alias),
        audit=DjangoAuditLog(alias),
        tenant_slug=tenant.slug,
        enabled=cfg["ENABLED"],
        default_language=cfg["DEFAULT_LANGUAGE"],
        deadline_seconds=cfg["DEADLINE_SECONDS"],
    )


def reply_suggestion_service() -> ReplySuggestionService:
    tenant = require_tenant()
    alias = tenant.db_alias
    cfg = settings.AI_PLATFORM
    return ReplySuggestionService(
        repository=DjangoReplyRepository(alias),
        conversations=CommunicationsConversationReader(),
        client=ai_platform_client(),
        scheduler=CeleryReplyScheduler(alias),
        uow=DjangoUnitOfWork(alias),
        outbox=DjangoEventOutbox(alias),
        audit=DjangoAuditLog(alias),
        tenant_slug=tenant.slug,
        enabled=cfg["ENABLED"],
        default_language=cfg["DEFAULT_LANGUAGE"],
        deadline_seconds=cfg["DEADLINE_SECONDS"],
    )


def call_insight_service() -> CallInsightService:
    tenant = require_tenant()
    alias = tenant.db_alias
    cfg = settings.AI_PLATFORM
    return CallInsightService(
        repository=DjangoInsightRepository(alias),
        calls=TelephonyCallReader(),
        client=ai_platform_client(),
        scheduler=CeleryInsightScheduler(alias),
        uow=DjangoUnitOfWork(alias),
        outbox=DjangoEventOutbox(alias),
        audit=DjangoAuditLog(alias),
        tenant_slug=tenant.slug,
        enabled=cfg["ENABLED"],
        deadline_seconds=cfg["DEADLINE_SECONDS"],
    )
