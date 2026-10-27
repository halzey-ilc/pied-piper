"""Asynchronous processing of ingested webhooks (``integrations`` queue)."""

from __future__ import annotations

import logging
from uuid import UUID

from celery import shared_task

from platform_core.infrastructure.messaging.stores import DjangoInboxStore
from platform_core.infrastructure.messaging.tasks import ResilientTask
from platform_core.infrastructure.tenancy.services import system_tenant_scope

logger = logging.getLogger(__name__)


@shared_task(base=ResilientTask, name="integrations.process_webhook", ignore_result=True)
def process_webhook(tenant_slug: str, provider: str, inbox_id: str) -> None:
    with system_tenant_scope(tenant_slug) as tenant:
        inbox = DjangoInboxStore(tenant.db_alias)
        if inbox.get_payload(UUID(inbox_id)) is None:
            logger.warning("webhook_inbox_row_missing", extra={"provider": provider})
            return
        # No provider-specific handlers are implemented yet (communications /
        # telephony modules). The message is marked processed so it is not retried.
        inbox.mark_processed(UUID(inbox_id))
        logger.info("webhook_processed", extra={"provider": provider})
