"""AI insight use cases. RBAC is enforced here; workers call submit/poll."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from modules.access_control.domain.rbac import (
    AccessPolicy,
    Permission,
    Principal,
    ResourceOwnership,
)
from modules.ai_insights.application.ports import (
    AiPlatformClient,
    CallReader,
    InsightRepository,
    InsightScheduler,
)
from modules.ai_insights.domain.insight import (
    CallInsight,
    InsightKind,
    InsightStatus,
    source_fingerprint,
)
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from platform_core.application.ports import EventOutbox
from shared.application.ports import UnitOfWork
from shared.application.retry import PermanentError
from shared.domain.errors import ConflictError, DomainValidationError, NotFoundError
from shared.domain.events import DomainEvent

logger = logging.getLogger(__name__)

AGGREGATE = "call_insight"
SCOPES = ("ai.jobs.create", "ai.jobs.read")


class AiDisabledError(ConflictError):
    code = "ai_disabled"


class CallInsightService:
    def __init__(
        self,
        repository: InsightRepository,
        calls: CallReader,
        client: AiPlatformClient,
        scheduler: InsightScheduler,
        uow: UnitOfWork,
        outbox: EventOutbox,
        audit: AuditLog,
        *,
        tenant_slug: str,
        enabled: bool,
        default_language: str = "ru",
        deadline_seconds: float = 900.0,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._repo = repository
        self._language = default_language
        self._calls = calls
        self._client = client
        self._scheduler = scheduler
        self._uow = uow
        self._outbox = outbox
        self._audit = audit
        self._tenant_slug = tenant_slug
        self._enabled = enabled
        self._deadline = deadline_seconds
        self._policy = policy or AccessPolicy()

    # --- request path ---------------------------------------------------------

    def request_call_summary(self, principal: Principal, call_id: UUID) -> tuple[CallInsight, bool]:
        """Queue a summary of a call the principal can see. Returns (insight, created).

        Asking again for an unchanged call returns the existing insight instead
        of starting a second analysis.
        """
        if not self._enabled:
            raise AiDisabledError("AI features are not enabled for this installation.")
        call = self._calls.get_visible(principal, call_id)
        self._policy.require(
            principal,
            Permission.AI_REQUEST,
            ResourceOwnership(owner_user_id=call.owner_user_id, team_id=call.team_id),
        )
        kind = InsightKind.CALL_SUMMARY
        with self._uow:
            existing = self._repo.find_reusable(call.id, kind, source_fingerprint(call.text))
            if existing is not None and not self._expired_pending(existing):
                return existing, False
            if existing is not None:
                # Never accepted by the AI Platform within the deadline: give up on it.
                self._finish(existing, existing.mark_failed("submit_timeout"))
            insight = CallInsight.request(
                call_id=call.id, kind=kind, requested_by=principal.user_id, source_text=call.text
            )
            self._repo.add(insight)
            self._record(principal.user_id, "call_insight.requested", insight)
            self._scheduler.schedule_submit(tenant_slug=self._tenant_slug, insight_id=insight.id)
        return insight, True

    def latest_call_summary(self, principal: Principal, call_id: UUID) -> CallInsight:
        """The newest summary of a call the principal can see."""
        call = self._calls.get_visible(principal, call_id)
        insight = self._repo.latest_for_call(call.id, InsightKind.CALL_SUMMARY)
        if insight is None:
            raise NotFoundError("No AI summary has been requested for this call.")
        return insight

    # --- worker steps (idempotent; safe to run again after a redelivery) -------

    def submit(self, insight_id: UUID) -> None:
        insight = self._repo.get(insight_id)
        if insight is None or insight.status is not InsightStatus.PENDING:
            return
        text = self._calls.text_for(insight.call_id)
        if not text or source_fingerprint(text) != insight.source_hash:
            # The call was deleted or edited after the request: do not analyse other text.
            with self._uow:
                self._finish(insight, insight.mark_failed("source_changed"))
            return
        try:
            state = self._client.submit(
                tenant_slug=self._tenant_slug,
                subject=f"user:{insight.requested_by}",
                job_type=insight.job_type,
                payload={
                    "transcript": text,
                    # The company's working language (AI_PLATFORM_LANGUAGE): the summary is
                    # written in it, and "not enough data" notices are not bilingual.
                    "language": self._language,
                    "call_ref": str(insight.call_id),
                },
                # The insight id makes a redelivered submit return the same AI job.
                idempotency_key=f"crm-insight-{insight.id}",
            )
        except PermanentError:
            logger.warning("ai_submit_rejected", extra={"insight_id": str(insight.id)})
            with self._uow:
                self._finish(insight, insight.mark_failed("submit_rejected"))
            return
        with self._uow:
            version = insight.version
            insight.mark_submitted(state.job_id)
            self._repo.save(insight, expected_version=version)
            self._scheduler.schedule_poll(
                tenant_slug=self._tenant_slug, insight_id=insight.id, delay_seconds=2.0
            )

    def poll(self, insight_id: UUID) -> None:
        insight = self._repo.get(insight_id)
        if (
            insight is None
            or insight.status is not InsightStatus.SUBMITTED
            or not insight.ai_job_id
        ):
            return
        try:
            state = self._client.fetch(
                tenant_slug=self._tenant_slug,
                subject=f"user:{insight.requested_by}",
                job_id=insight.ai_job_id,
            )
        except PermanentError:
            with self._uow:
                self._finish(insight, insight.mark_failed("fetch_rejected"))
            return
        if not state.is_terminal:
            age = self._age_seconds(insight)
            if age > self._deadline:
                with self._uow:
                    self._finish(insight, insight.mark_failed("timeout"))
                return
            self._scheduler.schedule_poll(
                tenant_slug=self._tenant_slug, insight_id=insight.id, delay_seconds=poll_delay(age)
            )
            return
        with self._uow:
            if state.status == "succeeded" and state.output is not None:
                generator = state.output.get("generator")
                try:
                    event = insight.mark_succeeded(
                        result=state.output,
                        schema_id=state.schema_id or "",
                        provider=state.provider or "unknown",
                        model=state.model or "unknown",
                        # Unknown provenance is treated as mock, never as a real model.
                        is_mock=not isinstance(generator, dict)
                        or generator.get("is_mock") is not False,
                    )
                except DomainValidationError:
                    logger.warning("ai_result_rejected", extra={"insight_id": str(insight.id)})
                    event = insight.mark_failed("unsupported_result")
            else:
                event = insight.mark_failed(state.error_code or state.status)
            self._finish(insight, event)

    # --- helpers --------------------------------------------------------------

    def _finish(self, insight: CallInsight, event: DomainEvent) -> None:
        """Persist a finished insight (caller holds the unit of work)."""
        self._repo.save(insight, expected_version=insight.version)
        self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(insight.id))
        self._record(insight.requested_by, f"call_insight.{insight.status.value}", insight)

    def _record(self, actor: UUID, action: str, insight: CallInsight) -> None:
        self._audit.record(
            AuditEntry(
                actor_user_id=actor,
                action=action,
                resource_type=AGGREGATE,
                resource_id=str(insight.id),
            )
        )

    def _age_seconds(self, insight: CallInsight) -> float:
        return (datetime.now(UTC) - insight.created_at).total_seconds()

    def _expired_pending(self, insight: CallInsight) -> bool:
        return (
            insight.status is InsightStatus.PENDING and self._age_seconds(insight) > self._deadline
        )


def poll_delay(age_seconds: float) -> float:
    """Poll quickly at first (most jobs finish in seconds), then back off."""
    if age_seconds < 30:
        return 2.0
    if age_seconds < 120:
        return 5.0
    return 15.0
