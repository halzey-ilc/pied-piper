"""AI worker steps (``integrations`` queue: outbound calls to the AI Platform).

Thin adapters over the ai_insights services. Every step is idempotent, so a
redelivered message (acks_late) does no harm. TransientError from the AI
Platform client is retried with backoff by ResilientTask.
"""

from __future__ import annotations

import logging
from uuid import UUID

from celery import shared_task

from modules.ai_insights.composition import (
    ai_report_service,
    call_insight_service,
    deal_brief_service,
    reply_suggestion_service,
)
from platform_core.infrastructure.messaging.tasks import ResilientTask
from platform_core.infrastructure.tenancy.services import system_tenant_scope, tenant_directory

logger = logging.getLogger(__name__)


@shared_task(base=ResilientTask, name="integrations.ai_submit_insight", ignore_result=True)
def submit_insight(tenant_slug: str, insight_id: str) -> None:
    with system_tenant_scope(tenant_slug):
        call_insight_service().submit(UUID(insight_id))


@shared_task(base=ResilientTask, name="integrations.ai_poll_insight", ignore_result=True)
def poll_insight(tenant_slug: str, insight_id: str) -> None:
    with system_tenant_scope(tenant_slug):
        call_insight_service().poll(UUID(insight_id))


@shared_task(base=ResilientTask, name="integrations.ai_submit_report", ignore_result=True)
def submit_report(tenant_slug: str, report_id: str) -> None:
    with system_tenant_scope(tenant_slug):
        ai_report_service().submit(UUID(report_id))


@shared_task(base=ResilientTask, name="integrations.ai_poll_report", ignore_result=True)
def poll_report(tenant_slug: str, report_id: str) -> None:
    with system_tenant_scope(tenant_slug):
        ai_report_service().poll(UUID(report_id))


@shared_task(base=ResilientTask, name="integrations.ai_submit_reply", ignore_result=True)
def submit_reply(tenant_slug: str, suggestion_id: str) -> None:
    with system_tenant_scope(tenant_slug):
        reply_suggestion_service().submit(UUID(suggestion_id))


@shared_task(base=ResilientTask, name="integrations.ai_poll_reply", ignore_result=True)
def poll_reply(tenant_slug: str, suggestion_id: str) -> None:
    with system_tenant_scope(tenant_slug):
        reply_suggestion_service().poll(UUID(suggestion_id))


@shared_task(base=ResilientTask, name="integrations.ai_submit_brief", ignore_result=True)
def submit_brief(tenant_slug: str, brief_id: str) -> None:
    with system_tenant_scope(tenant_slug):
        deal_brief_service().submit(UUID(brief_id))


@shared_task(base=ResilientTask, name="integrations.ai_poll_brief", ignore_result=True)
def poll_brief(tenant_slug: str, brief_id: str) -> None:
    with system_tenant_scope(tenant_slug):
        deal_brief_service().poll(UUID(brief_id))


@shared_task(base=ResilientTask, name="integrations.ai_daily_digests", ignore_result=True)
def daily_digests() -> dict[str, int]:
    """Queue today's operations digests in every active tenant (no-op when AI is off)."""
    created = 0
    for locator in tenant_directory().list_routable():
        try:
            with system_tenant_scope(locator.slug):
                created += ai_report_service().create_daily_digests()
        except Exception:
            # One broken tenant must not stop the others; the next run retries it.
            logger.exception("ai_daily_digest_failed", extra={"tenant_slug": locator.slug})
    return {"created": created}
