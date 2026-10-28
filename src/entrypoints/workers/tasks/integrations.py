"""Asynchronous processing of ingested webhooks (``integrations`` queue).

Reads the stored inbox payload, turns it into a normalized inbound message
(communications parsers) and appends it to a conversation. Unparseable or
non-message payloads (e.g. delivery-status callbacks) are acknowledged and
ignored. Ingest is idempotent.
"""

from __future__ import annotations

import logging
from uuid import UUID

from celery import shared_task

from modules.communications.composition import conversation_service
from modules.communications.infrastructure.parsers import parse_inbound
from modules.integrations.domain.providers import Provider
from platform_core.infrastructure.messaging.stores import DjangoInboxStore
from platform_core.infrastructure.messaging.tasks import ResilientTask
from platform_core.infrastructure.tenancy.services import system_tenant_scope

logger = logging.getLogger(__name__)


@shared_task(base=ResilientTask, name="integrations.process_webhook", ignore_result=True)
def process_webhook(tenant_slug: str, provider: str, inbox_id: str) -> None:
    with system_tenant_scope(tenant_slug) as tenant:
        inbox = DjangoInboxStore(tenant.db_alias)
        payload = inbox.get_payload(UUID(inbox_id))
        if payload is None:
            logger.warning("webhook_inbox_row_missing", extra={"provider": provider})
            return
        inbound = parse_inbound(Provider(provider), payload)
        if inbound is not None:
            created = conversation_service().ingest_inbound(inbound)
            logger.info(
                "webhook_message_ingested",
                extra={"provider": provider, "message_created": created},
            )
        inbox.mark_processed(UUID(inbox_id))
