from __future__ import annotations

from uuid import UUID

from celery import current_app
from django.db import transaction

from platform_core.application.context import current_correlation_id

SUBMIT_TASK = "integrations.ai_submit_insight"
POLL_TASK = "integrations.ai_poll_insight"
# Outbound calls to an external service: the existing integrations queue.
QUEUE = "integrations"


class CeleryInsightScheduler:
    """Enqueues worker steps only after the insight row has been committed."""

    def __init__(self, using: str) -> None:
        self.using = using

    def schedule_submit(self, *, tenant_slug: str, insight_id: UUID) -> None:
        self._send(SUBMIT_TASK, tenant_slug, insight_id, countdown=None)

    def schedule_poll(self, *, tenant_slug: str, insight_id: UUID, delay_seconds: float) -> None:
        self._send(POLL_TASK, tenant_slug, insight_id, countdown=delay_seconds)

    def _send(self, task: str, tenant_slug: str, insight_id: UUID, countdown: float | None) -> None:
        correlation_id = current_correlation_id() or ""

        def _go() -> None:
            current_app.send_task(
                task,
                kwargs={"tenant_slug": tenant_slug, "insight_id": str(insight_id)},
                queue=QUEUE,
                countdown=countdown,
                headers={"crm_correlation_id": correlation_id},
            )

        transaction.on_commit(_go, using=self.using)
