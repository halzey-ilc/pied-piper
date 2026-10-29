"""Deal briefs: preparation for the next conversation with a client (AI Sales Coach).

The brief is built from what the requesting user can see, so it is personal:
each user reads only their own briefs.
"""

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
from modules.ai_insights.application.deal_brief_ports import (
    DealBriefRepository,
    DealBriefScheduler,
    DealContextReader,
)
from modules.ai_insights.application.ports import AiPlatformClient
from modules.ai_insights.application.service import AiDisabledError, poll_delay
from modules.ai_insights.domain.deal_brief import JOB_TYPE, DealBrief, build_payload
from modules.ai_insights.domain.insight import InsightStatus
from modules.ai_insights.domain.report import payload_fingerprint, validate_language
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from platform_core.application.ports import EventOutbox
from shared.application.ports import UnitOfWork
from shared.application.retry import PermanentError
from shared.domain.errors import DomainValidationError, NotFoundError
from shared.domain.events import DomainEvent

logger = logging.getLogger(__name__)

AGGREGATE = "deal_brief"


class DealBriefService:
    def __init__(
        self,
        repository: DealBriefRepository,
        deals: DealContextReader,
        client: AiPlatformClient,
        scheduler: DealBriefScheduler,
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
        self._deals = deals
        self._client = client
        self._scheduler = scheduler
        self._uow = uow
        self._outbox = outbox
        self._audit = audit
        self._tenant_slug = tenant_slug
        self._enabled = enabled
        self._language = default_language
        self._deadline = deadline_seconds
        self._policy = policy or AccessPolicy()

    def request_brief(
        self, principal: Principal, deal_id: UUID, *, language: str | None = None
    ) -> tuple[DealBrief, bool]:
        """Queue a brief for a deal the principal can see. Returns (brief, created)."""
        if not self._enabled:
            raise AiDisabledError("AI features are not enabled for this installation.")
        today = datetime.now(UTC).date()
        context = self._deals.read(principal, deal_id, today)
        self._policy.require(
            principal,
            Permission.AI_REQUEST,
            ResourceOwnership(owner_user_id=context.owner_user_id, team_id=context.team_id),
        )
        payload = build_payload(
            language=validate_language(language or self._language),
            today=today,
            deal=context.facts,
            history=context.history,
        )
        with self._uow:
            existing = self._repo.find_reusable(
                context.facts.id, principal.user_id, payload_fingerprint(payload)
            )
            if existing is not None and not self._expired_pending(existing):
                return existing, False
            if existing is not None:
                self._finish(existing, existing.mark_failed("submit_timeout"))
            brief = DealBrief.request(
                deal_id=context.facts.id, requested_by=principal.user_id, payload=payload
            )
            self._repo.add(brief)
            self._record(principal.user_id, "deal_brief.requested", brief)
            self._scheduler.schedule_submit(tenant_slug=self._tenant_slug, brief_id=brief.id)
        return brief, True

    def latest(self, principal: Principal, deal_id: UUID) -> DealBrief:
        """The principal's own newest brief for a deal they can still see."""
        context = self._deals.read(principal, deal_id, datetime.now(UTC).date())
        brief = self._repo.latest_for(context.facts.id, principal.user_id)
        if brief is None:
            raise NotFoundError("No brief has been requested for this deal.")
        return brief

    # --- worker steps (idempotent) ----------------------------------------------

    def submit(self, brief_id: UUID) -> None:
        brief = self._repo.get(brief_id)
        if brief is None or brief.status is not InsightStatus.PENDING or brief.payload is None:
            return
        try:
            state = self._client.submit(
                tenant_slug=self._tenant_slug,
                subject=f"user:{brief.requested_by}",
                job_type=JOB_TYPE,
                payload=brief.payload,
                idempotency_key=f"crm-brief-{brief.id}",
            )
        except PermanentError:
            logger.warning("ai_brief_submit_rejected", extra={"brief_id": str(brief.id)})
            with self._uow:
                self._finish(brief, brief.mark_failed("submit_rejected"))
            return
        with self._uow:
            version = brief.version
            brief.mark_submitted(state.job_id)
            self._repo.save(brief, expected_version=version)
            self._scheduler.schedule_poll(
                tenant_slug=self._tenant_slug, brief_id=brief.id, delay_seconds=2.0
            )

    def poll(self, brief_id: UUID) -> None:
        brief = self._repo.get(brief_id)
        if brief is None or brief.status is not InsightStatus.SUBMITTED or not brief.ai_job_id:
            return
        try:
            state = self._client.fetch(
                tenant_slug=self._tenant_slug,
                subject=f"user:{brief.requested_by}",
                job_id=brief.ai_job_id,
            )
        except PermanentError:
            with self._uow:
                self._finish(brief, brief.mark_failed("fetch_rejected"))
            return
        if not state.is_terminal:
            age = self._age_seconds(brief)
            if age > self._deadline:
                with self._uow:
                    self._finish(brief, brief.mark_failed("timeout"))
                return
            self._scheduler.schedule_poll(
                tenant_slug=self._tenant_slug, brief_id=brief.id, delay_seconds=poll_delay(age)
            )
            return
        with self._uow:
            if state.status == "succeeded" and state.output is not None:
                generator = state.output.get("generator")
                try:
                    event = brief.mark_succeeded(
                        result=state.output,
                        schema_id=state.schema_id or "",
                        provider=state.provider or "unknown",
                        model=state.model or "unknown",
                        # Unknown provenance is treated as mock, never as a real model.
                        is_mock=not isinstance(generator, dict)
                        or generator.get("is_mock") is not False,
                    )
                except DomainValidationError:
                    event = brief.mark_failed("unsupported_result")
            else:
                event = brief.mark_failed(state.error_code or state.status)
            self._finish(brief, event)

    # --- helpers ----------------------------------------------------------------

    def _finish(self, brief: DealBrief, event: DomainEvent) -> None:
        self._repo.save(brief, expected_version=brief.version)
        self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(brief.id))
        self._record(brief.requested_by, f"deal_brief.{brief.status.value}", brief)

    def _record(self, actor: UUID, action: str, brief: DealBrief) -> None:
        self._audit.record(
            AuditEntry(
                actor_user_id=actor,
                action=action,
                resource_type=AGGREGATE,
                resource_id=str(brief.id),
            )
        )

    def _age_seconds(self, brief: DealBrief) -> float:
        return (datetime.now(UTC) - brief.created_at).total_seconds()

    def _expired_pending(self, brief: DealBrief) -> bool:
        return brief.status is InsightStatus.PENDING and self._age_seconds(brief) > self._deadline
