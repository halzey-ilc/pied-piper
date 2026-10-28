"""Wires the AI insight use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from functools import cache

from django.conf import settings

from modules.ai_insights.application.digest_service import OperationsDigestService
from modules.ai_insights.application.service import CallInsightService
from modules.ai_insights.infrastructure.calls import TelephonyCallReader
from modules.ai_insights.infrastructure.client import HttpAiPlatformClient
from modules.ai_insights.infrastructure.digest_repository import DjangoDigestRepository
from modules.ai_insights.infrastructure.digest_sources import (
    OperationsFindingsSource,
    OrganizationMemberDirectory,
)
from modules.ai_insights.infrastructure.repository import DjangoInsightRepository
from modules.ai_insights.infrastructure.scheduler import (
    CeleryDigestScheduler,
    CeleryInsightScheduler,
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


def operations_digest_service() -> OperationsDigestService:
    tenant = require_tenant()
    alias = tenant.db_alias
    cfg = settings.AI_PLATFORM
    return OperationsDigestService(
        repository=DjangoDigestRepository(alias),
        findings=OperationsFindingsSource(),
        members=OrganizationMemberDirectory(),
        client=ai_platform_client(),
        scheduler=CeleryDigestScheduler(alias),
        uow=DjangoUnitOfWork(alias),
        outbox=DjangoEventOutbox(alias),
        audit=DjangoAuditLog(alias),
        tenant_slug=tenant.slug,
        tenant_id=tenant.tenant_id,
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
