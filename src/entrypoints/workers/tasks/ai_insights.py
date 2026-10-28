"""AI insight worker steps (``integrations`` queue: outbound calls to the AI Platform).

Thin adapters over CallInsightService. Both steps are idempotent, so a
redelivered message (acks_late) does no harm. TransientError from the AI
Platform client is retried with backoff by ResilientTask.
"""

from __future__ import annotations

import logging
from uuid import UUID

from celery import shared_task

from modules.ai_insights.composition import call_insight_service
from platform_core.infrastructure.messaging.tasks import ResilientTask
from platform_core.infrastructure.tenancy.services import system_tenant_scope

logger = logging.getLogger(__name__)


@shared_task(base=ResilientTask, name="integrations.ai_submit_insight", ignore_result=True)
def submit_insight(tenant_slug: str, insight_id: str) -> None:
    with system_tenant_scope(tenant_slug):
        call_insight_service().submit(UUID(insight_id))


@shared_task(base=ResilientTask, name="integrations.ai_poll_insight", ignore_result=True)
def poll_insight(tenant_slug: str, insight_id: str) -> None:
    with system_tenant_scope(tenant_slug):
        call_insight_service().poll(UUID(insight_id))
