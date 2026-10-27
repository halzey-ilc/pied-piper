from __future__ import annotations

from uuid import UUID

from celery import current_app
from django.db import transaction

from modules.integrations.domain.providers import Provider
from platform_core.application.context import current_correlation_id
from platform_core.infrastructure.observability.celery_signals import CORRELATION_HEADER

PROCESS_WEBHOOK_TASK = "integrations.process_webhook"


class CeleryWebhookScheduler:
    """Enqueues processing only after the inbox row has been committed."""

    def __init__(self, using: str) -> None:
        self.using = using

    def schedule(self, *, tenant_slug: str, provider: Provider, inbox_id: UUID) -> None:
        correlation_id = current_correlation_id() or ""

        def _send() -> None:
            current_app.send_task(
                PROCESS_WEBHOOK_TASK,
                kwargs={
                    "tenant_slug": tenant_slug,
                    "provider": provider.value,
                    "inbox_id": str(inbox_id),
                },
                headers={CORRELATION_HEADER: correlation_id},
            )

        transaction.on_commit(_send, using=self.using)
