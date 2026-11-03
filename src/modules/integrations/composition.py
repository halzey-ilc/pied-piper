from __future__ import annotations

from django.conf import settings

from modules.integrations.application.status import IntegrationStatusService
from modules.integrations.application.webhooks import IngestWebhook
from modules.integrations.domain.providers import Provider
from modules.integrations.infrastructure.events import DjangoInboundEvents
from modules.integrations.infrastructure.providers import (
    ConfiguredProviderSpec,
    build_provider_specs,
)
from modules.integrations.infrastructure.scheduler import CeleryWebhookScheduler
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.stores import DjangoInboxStore
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def provider_spec(provider: Provider) -> ConfiguredProviderSpec:
    return build_provider_specs(settings.INTEGRATIONS["SECRETS"])[provider]


def integration_status_service() -> IntegrationStatusService:
    tenant = require_tenant()
    secrets = settings.INTEGRATIONS["SECRETS"]
    return IntegrationStatusService(
        inbound_secrets={p: secrets.get(p.value) is not None for p in Provider},
        events=DjangoInboundEvents(tenant.db_alias),
        tenant_slug=tenant.slug,
    )


def ingest_webhook() -> IngestWebhook:
    alias = require_tenant().db_alias
    return IngestWebhook(
        inbox=DjangoInboxStore(alias),
        uow=DjangoUnitOfWork(alias),
        scheduler=CeleryWebhookScheduler(alias),
    )
