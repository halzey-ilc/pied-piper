from __future__ import annotations

from django.conf import settings

from modules.integrations.application.webhooks import IngestWebhook
from modules.integrations.domain.providers import Provider
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


def ingest_webhook() -> IngestWebhook:
    alias = require_tenant().db_alias
    return IngestWebhook(
        inbox=DjangoInboxStore(alias),
        uow=DjangoUnitOfWork(alias),
        scheduler=CeleryWebhookScheduler(alias),
    )
