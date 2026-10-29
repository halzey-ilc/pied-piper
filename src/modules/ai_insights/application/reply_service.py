"""Reply suggestions for customer conversations (AI Communication Assistant).

A user who can see a conversation asks for a suggestion; a worker sends the
latest messages to the AI Platform and stores the classification and draft.
Nothing is sent to the customer: the draft is shown to people, who send their
own message through the conversation endpoints.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from modules.access_control.domain.rbac import (
    AccessPolicy,
    Permission,
    Principal,
    ResourceOwnership,
)
from modules.ai_insights.application.ports import AiPlatformClient
from modules.ai_insights.application.reply_ports import (
    ConversationReader,
    ReplyRepository,
    ReplyScheduler,
)
from modules.ai_insights.application.service import AiDisabledError, poll_delay
from modules.ai_insights.domain.insight import InsightStatus
from modules.ai_insights.domain.reply import (
    JOB_TYPE,
    MAX_MESSAGES,
    ReplySuggestion,
    build_payload,
)
from modules.ai_insights.domain.report import payload_fingerprint, validate_language
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from platform_core.application.ports import EventOutbox
from shared.application.ports import UnitOfWork
from shared.application.retry import PermanentError
from shared.domain.errors import DomainValidationError, NotFoundError
from shared.domain.events import DomainEvent

logger = logging.getLogger(__name__)

AGGREGATE = "reply_suggestion"


class ReplySuggestionService:
    def __init__(
        self,
        repository: ReplyRepository,
        conversations: ConversationReader,
        client: AiPlatformClient,
        scheduler: ReplyScheduler,
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
        self._conversations = conversations
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

    # --- request path ---------------------------------------------------------

    def request_reply(
        self, principal: Principal, conversation_id: UUID, *, language: str | None = None
    ) -> tuple[ReplySuggestion, bool]:
        """Queue a suggestion for the customer's latest message. Returns (suggestion, created).

        Asking again while the conversation is unchanged returns the existing one.
        """
        if not self._enabled:
            raise AiDisabledError("AI features are not enabled for this installation.")
        conversation = self._conversations.get_visible(principal, conversation_id)
        self._policy.require(
            principal,
            Permission.AI_REQUEST,
            ResourceOwnership(
                owner_user_id=conversation.owner_user_id, team_id=conversation.team_id
            ),
        )
        # Raises DomainValidationError when the last message is the company's.
        payload = self._payload(conversation.id, conversation.channel, language or self._language)
        with self._uow:
            existing = self._repo.find_reusable(conversation.id, payload_fingerprint(payload))
            if existing is not None and not self._expired_pending(existing):
                return existing, False
            if existing is not None:
                self._finish(existing, existing.mark_failed("submit_timeout"))
            suggestion = ReplySuggestion.request(
                conversation_id=conversation.id, requested_by=principal.user_id, payload=payload
            )
            self._repo.add(suggestion)
            self._record(principal.user_id, "reply_suggestion.requested", suggestion)
            self._scheduler.schedule_submit(
                tenant_slug=self._tenant_slug, suggestion_id=suggestion.id
            )
        return suggestion, True

    def latest(self, principal: Principal, conversation_id: UUID) -> ReplySuggestion:
        """The newest suggestion for a conversation the principal can see."""
        conversation = self._conversations.get_visible(principal, conversation_id)
        suggestion = self._repo.latest_for_conversation(conversation.id)
        if suggestion is None:
            raise NotFoundError("No reply suggestion has been requested for this conversation.")
        return suggestion

    # --- worker steps (idempotent) ----------------------------------------------

    def submit(self, suggestion_id: UUID) -> None:
        suggestion = self._repo.get(suggestion_id)
        if suggestion is None or suggestion.status is not InsightStatus.PENDING:
            return
        channel = self._conversations.channel_of(suggestion.conversation_id)
        try:
            payload = (
                None
                if channel is None
                else self._payload(suggestion.conversation_id, channel, suggestion.language)
            )
        except DomainValidationError:
            payload = None  # the company has replied since the request
        if payload is None or payload_fingerprint(payload) != suggestion.source_hash:
            # New messages since the request: do not answer a different conversation.
            with self._uow:
                self._finish(suggestion, suggestion.mark_failed("source_changed"))
            return
        try:
            state = self._client.submit(
                tenant_slug=self._tenant_slug,
                subject=f"user:{suggestion.requested_by}",
                job_type=JOB_TYPE,
                payload=payload,
                idempotency_key=f"crm-reply-{suggestion.id}",
            )
        except PermanentError:
            logger.warning("ai_reply_submit_rejected", extra={"suggestion_id": str(suggestion.id)})
            with self._uow:
                self._finish(suggestion, suggestion.mark_failed("submit_rejected"))
            return
        with self._uow:
            version = suggestion.version
            suggestion.mark_submitted(state.job_id)
            self._repo.save(suggestion, expected_version=version)
            self._scheduler.schedule_poll(
                tenant_slug=self._tenant_slug, suggestion_id=suggestion.id, delay_seconds=2.0
            )

    def poll(self, suggestion_id: UUID) -> None:
        suggestion = self._repo.get(suggestion_id)
        if (
            suggestion is None
            or suggestion.status is not InsightStatus.SUBMITTED
            or not suggestion.ai_job_id
        ):
            return
        try:
            state = self._client.fetch(
                tenant_slug=self._tenant_slug,
                subject=f"user:{suggestion.requested_by}",
                job_id=suggestion.ai_job_id,
            )
        except PermanentError:
            with self._uow:
                self._finish(suggestion, suggestion.mark_failed("fetch_rejected"))
            return
        if not state.is_terminal:
            age = self._age_seconds(suggestion)
            if age > self._deadline:
                with self._uow:
                    self._finish(suggestion, suggestion.mark_failed("timeout"))
                return
            self._scheduler.schedule_poll(
                tenant_slug=self._tenant_slug,
                suggestion_id=suggestion.id,
                delay_seconds=poll_delay(age),
            )
            return
        with self._uow:
            if state.status == "succeeded" and state.output is not None:
                generator = state.output.get("generator")
                try:
                    event = suggestion.mark_succeeded(
                        result=state.output,
                        schema_id=state.schema_id or "",
                        provider=state.provider or "unknown",
                        model=state.model or "unknown",
                        # Unknown provenance is treated as mock, never as a real model.
                        is_mock=not isinstance(generator, dict)
                        or generator.get("is_mock") is not False,
                    )
                except DomainValidationError:
                    event = suggestion.mark_failed("unsupported_result")
            else:
                event = suggestion.mark_failed(state.error_code or state.status)
            self._finish(suggestion, event)

    # --- helpers ----------------------------------------------------------------

    def _payload(self, conversation_id: UUID, channel: str, language: str) -> dict[str, Any]:
        messages = self._conversations.recent_messages(conversation_id, MAX_MESSAGES)
        return build_payload(
            language=validate_language(language), channel=channel, messages=messages
        )

    def _finish(self, suggestion: ReplySuggestion, event: DomainEvent) -> None:
        self._repo.save(suggestion, expected_version=suggestion.version)
        self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(suggestion.id))
        self._record(
            suggestion.requested_by, f"reply_suggestion.{suggestion.status.value}", suggestion
        )

    def _record(self, actor: UUID, action: str, suggestion: ReplySuggestion) -> None:
        self._audit.record(
            AuditEntry(
                actor_user_id=actor,
                action=action,
                resource_type=AGGREGATE,
                resource_id=str(suggestion.id),
            )
        )

    def _age_seconds(self, suggestion: ReplySuggestion) -> float:
        return (datetime.now(UTC) - suggestion.created_at).total_seconds()

    def _expired_pending(self, suggestion: ReplySuggestion) -> bool:
        return (
            suggestion.status is InsightStatus.PENDING
            and self._age_seconds(suggestion) > self._deadline
        )
