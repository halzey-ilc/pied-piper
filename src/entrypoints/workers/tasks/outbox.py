"""Outbox relay. Runs every 15s via Celery Beat.

Visits the control plane and then each active tenant database *sequentially*,
closing every tenant connection before moving on. The relay itself is in
platform_core.application.messaging.OutboxRelay.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from platform_core.application.context import TenantContext
from platform_core.application.messaging import OutboxRelay, RelayStats
from platform_core.infrastructure.messaging.publisher import CeleryMessagePublisher
from platform_core.infrastructure.messaging.stores import DjangoOutboxStore
from platform_core.infrastructure.messaging.tasks import ResilientTask
from platform_core.infrastructure.observability.metrics import OUTBOX_EVENTS
from platform_core.infrastructure.tenancy.services import system_tenant_scope, tenant_directory
from shared.application.retry import RetryPolicy
from shared.infrastructure.unit_of_work import DjangoUnitOfWork

logger = logging.getLogger(__name__)
BATCH_SIZE = 100
POLICY = RetryPolicy(max_attempts=10, base_delay_seconds=2, max_delay_seconds=900)


def _relay(alias: str, tenant: TenantContext | None) -> RelayStats:
    relay = OutboxRelay(
        DjangoOutboxStore(alias, tenant.slug if tenant else None),
        CeleryMessagePublisher(),
        DjangoUnitOfWork(alias),
        POLICY,
    )
    stats = relay.relay_once(BATCH_SIZE)
    OUTBOX_EVENTS.labels("published").inc(stats.published)
    OUTBOX_EVENTS.labels("retried").inc(stats.retried)
    OUTBOX_EVENTS.labels("failed").inc(stats.failed)
    return stats


@shared_task(base=ResilientTask, name="platform.outbox.relay_all", ignore_result=True)
def relay_all() -> dict[str, Any]:
    totals = {"published": 0, "retried": 0, "failed": 0, "databases": 1}
    stats = _relay("default", None)
    for key in ("published", "retried", "failed"):
        totals[key] += getattr(stats, key)
    for locator in tenant_directory().list_routable():
        try:
            with system_tenant_scope(locator.slug) as tenant:
                stats = _relay(tenant.db_alias, tenant)
        except Exception:
            # One broken tenant database must not stop the relay for the others.
            logger.exception("outbox_relay_tenant_failed", extra={"tenant_slug": locator.slug})
            continue
        totals["databases"] += 1
        for key in ("published", "retried", "failed"):
            totals[key] += getattr(stats, key)
    return totals
