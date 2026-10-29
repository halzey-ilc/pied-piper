from __future__ import annotations

from uuid import UUID

from celery import current_app
from django.db import transaction

from platform_core.application.context import current_correlation_id

SUBMIT_TASK = "integrations.ai_submit_insight"
POLL_TASK = "integrations.ai_poll_insight"
SUBMIT_REPORT_TASK = "integrations.ai_submit_report"
POLL_REPORT_TASK = "integrations.ai_poll_report"
SUBMIT_REPLY_TASK = "integrations.ai_submit_reply"
POLL_REPLY_TASK = "integrations.ai_poll_reply"
SUBMIT_BRIEF_TASK = "integrations.ai_submit_brief"
POLL_BRIEF_TASK = "integrations.ai_poll_brief"
SUBMIT_AGENT_TASK = "integrations.ai_submit_agent_run"
POLL_AGENT_TASK = "integrations.ai_poll_agent_run"
# Outbound calls to an external service: the existing integrations queue.
QUEUE = "integrations"


def _send_on_commit(using: str, task: str, kwargs: dict[str, str], countdown: float | None) -> None:
    correlation_id = current_correlation_id() or ""

    def _go() -> None:
        current_app.send_task(
            task,
            kwargs=kwargs,
            queue=QUEUE,
            countdown=countdown,
            headers={"crm_correlation_id": correlation_id},
        )

    transaction.on_commit(_go, using=using)


class CeleryInsightScheduler:
    """Enqueues call-insight steps only after the insight row has been committed."""

    def __init__(self, using: str) -> None:
        self.using = using

    def schedule_submit(self, *, tenant_slug: str, insight_id: UUID) -> None:
        _send_on_commit(
            self.using,
            SUBMIT_TASK,
            {"tenant_slug": tenant_slug, "insight_id": str(insight_id)},
            None,
        )

    def schedule_poll(self, *, tenant_slug: str, insight_id: UUID, delay_seconds: float) -> None:
        _send_on_commit(
            self.using,
            POLL_TASK,
            {"tenant_slug": tenant_slug, "insight_id": str(insight_id)},
            delay_seconds,
        )


class CeleryReportScheduler:
    """Enqueues AI report steps only after the report row has been committed."""

    def __init__(self, using: str) -> None:
        self.using = using

    def schedule_submit(self, *, tenant_slug: str, report_id: UUID) -> None:
        _send_on_commit(
            self.using,
            SUBMIT_REPORT_TASK,
            {"tenant_slug": tenant_slug, "report_id": str(report_id)},
            None,
        )

    def schedule_poll(self, *, tenant_slug: str, report_id: UUID, delay_seconds: float) -> None:
        _send_on_commit(
            self.using,
            POLL_REPORT_TASK,
            {"tenant_slug": tenant_slug, "report_id": str(report_id)},
            delay_seconds,
        )


class CeleryReplyScheduler:
    """Enqueues reply-suggestion steps only after the suggestion row has been committed."""

    def __init__(self, using: str) -> None:
        self.using = using

    def schedule_submit(self, *, tenant_slug: str, suggestion_id: UUID) -> None:
        _send_on_commit(
            self.using,
            SUBMIT_REPLY_TASK,
            {"tenant_slug": tenant_slug, "suggestion_id": str(suggestion_id)},
            None,
        )

    def schedule_poll(self, *, tenant_slug: str, suggestion_id: UUID, delay_seconds: float) -> None:
        _send_on_commit(
            self.using,
            POLL_REPLY_TASK,
            {"tenant_slug": tenant_slug, "suggestion_id": str(suggestion_id)},
            delay_seconds,
        )


class CeleryDealBriefScheduler:
    """Enqueues deal-brief steps only after the brief row has been committed."""

    def __init__(self, using: str) -> None:
        self.using = using

    def schedule_submit(self, *, tenant_slug: str, brief_id: UUID) -> None:
        _send_on_commit(
            self.using,
            SUBMIT_BRIEF_TASK,
            {"tenant_slug": tenant_slug, "brief_id": str(brief_id)},
            None,
        )

    def schedule_poll(self, *, tenant_slug: str, brief_id: UUID, delay_seconds: float) -> None:
        _send_on_commit(
            self.using,
            POLL_BRIEF_TASK,
            {"tenant_slug": tenant_slug, "brief_id": str(brief_id)},
            delay_seconds,
        )


class CeleryAgentScheduler:
    """Enqueues assistant-run steps only after the run row has been committed."""

    def __init__(self, using: str) -> None:
        self.using = using

    def schedule_submit(self, *, tenant_slug: str, run_id: UUID) -> None:
        _send_on_commit(
            self.using, SUBMIT_AGENT_TASK, {"tenant_slug": tenant_slug, "run_id": str(run_id)}, None
        )

    def schedule_poll(self, *, tenant_slug: str, run_id: UUID, delay_seconds: float) -> None:
        _send_on_commit(
            self.using,
            POLL_AGENT_TASK,
            {"tenant_slug": tenant_slug, "run_id": str(run_id)},
            delay_seconds,
        )
