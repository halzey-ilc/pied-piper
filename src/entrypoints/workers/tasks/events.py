"""Dispatch of domain events published from the outbox to registered handlers.

Each handler runs through the inbox (IdempotentConsumer), so redelivery of the
same event does not repeat its effects.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from platform_core.application.messaging import IdempotentConsumer, event_handlers
from platform_core.infrastructure.messaging.stores import DjangoInboxStore
from platform_core.infrastructure.messaging.tasks import ResilientTask
from platform_core.infrastructure.tenancy.services import system_tenant_scope
from shared.infrastructure.unit_of_work import DjangoUnitOfWork

logger = logging.getLogger(__name__)


def _dispatch(alias: str, envelope: dict[str, Any]) -> int:
    handlers = event_handlers.handlers_for(envelope["event_type"])
    consumer = IdempotentConsumer(DjangoInboxStore(alias), DjangoUnitOfWork(alias))
    for name, handler in handlers.items():
        # Handlers receive the full envelope (event_type, event_id, occurred_at,
        # payload, ...), not just the business payload, so read models can record
        # when the event happened and dedupe on its id.
        consumer.consume(
            source="event",
            message_id=envelope["event_id"],
            consumer=name,
            payload=envelope,
            correlation_id=envelope.get("correlation_id"),
            handler=handler,
        )
    return len(handlers)


@shared_task(base=ResilientTask, name="platform.events.dispatch", ignore_result=True)
def dispatch(envelope: dict[str, Any]) -> int:
    tenant_slug = envelope.get("tenant_slug")
    if tenant_slug:
        with system_tenant_scope(tenant_slug) as tenant:
            handled = _dispatch(tenant.db_alias, envelope)
    else:
        handled = _dispatch("default", envelope)
    if handled == 0:
        logger.info("event_without_handlers", extra={"event_type": envelope["event_type"]})
    return handled
