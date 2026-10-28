"""AI explanation of operations findings (AI Operations).

The CRM's rules decide what is wrong; the AI Platform only explains it. A user
gets a digest of the findings they can see, on demand or once a day.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal, Role
from modules.ai_insights.application.digest_ports import (
    DigestRepository,
    DigestScheduler,
    FindingsSnapshot,
    FindingsSource,
    MemberDirectory,
)
from modules.ai_insights.application.ports import AiPlatformClient
from modules.ai_insights.application.service import AiDisabledError, poll_delay
from modules.ai_insights.domain.digest import JOB_TYPE, DigestTrigger, OperationsDigest
from modules.ai_insights.domain.insight import InsightStatus
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from platform_core.application.ports import EventOutbox
from shared.application.ports import UnitOfWork
from shared.application.retry import PermanentError
from shared.domain.errors import DomainValidationError, NotFoundError
from shared.domain.events import DomainEvent

logger = logging.getLogger(__name__)

AGGREGATE = "operations_digest"


class OperationsDigestService:
    def __init__(
        self,
        repository: DigestRepository,
        findings: FindingsSource,
        members: MemberDirectory,
        client: AiPlatformClient,
        scheduler: DigestScheduler,
        uow: UnitOfWork,
        outbox: EventOutbox,
        audit: AuditLog,
        *,
        tenant_slug: str,
        tenant_id: UUID,
        enabled: bool,
        default_language: str = "ru",
        deadline_seconds: float = 900.0,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._repo = repository
        self._findings = findings
        self._members = members
        self._client = client
        self._scheduler = scheduler
        self._uow = uow
        self._outbox = outbox
        self._audit = audit
        self._tenant_slug = tenant_slug
        self._tenant_id = tenant_id
        self._enabled = enabled
        self._language = default_language
        self._deadline = deadline_seconds
        self._policy = policy or AccessPolicy()

    # --- request path ---------------------------------------------------------

    def request(
        self, principal: Principal, *, language: str | None = None
    ) -> tuple[OperationsDigest, bool]:
        """Explain the principal's current findings. Returns (digest, created)."""
        if not self._enabled:
            raise AiDisabledError("AI features are not enabled for this installation.")
        self._policy.require(principal, Permission.AI_REQUEST)
        return self._create(principal, DigestTrigger.MANUAL, language or self._language)

    def latest(self, principal: Principal) -> OperationsDigest:
        """The principal's own newest digest; digests are never shared between users."""
        digest = self._repo.latest_for_user(principal.user_id)
        if digest is None:
            raise NotFoundError("No operations digest yet.")
        return digest

    # --- daily run (worker, no request) ----------------------------------------

    def create_daily(self) -> int:
        """One digest per member allowed to use AI who has findings today. Idempotent."""
        if not self._enabled:
            return 0
        today = datetime.now(UTC).date()
        created = 0
        for member in self._members.active_members(self._tenant_id):
            try:
                principal = Principal(
                    user_id=member.user_id, tenant_id=self._tenant_id, role=Role(member.role)
                )
            except ValueError:
                continue
            if self._policy.scope_for(principal, Permission.AI_REQUEST) is None:
                continue
            if self._repo.exists_for_day(principal.user_id, DigestTrigger.DAILY, today):
                continue
            snapshot = self._findings.snapshot_for(principal)
            if not snapshot.findings:
                continue  # nothing to explain; no empty daily digests
            _, was_created = self._create(
                principal, DigestTrigger.DAILY, self._language, snapshot=snapshot
            )
            created += int(was_created)
        return created

    # --- worker steps -----------------------------------------------------------

    def submit(self, digest_id: UUID) -> None:
        digest = self._repo.get(digest_id)
        if digest is None or digest.status is not InsightStatus.PENDING:
            return
        try:
            state = self._client.submit(
                tenant_slug=self._tenant_slug,
                subject=f"user:{digest.requested_by}",
                job_type=JOB_TYPE,
                payload=digest.payload,
                idempotency_key=f"crm-digest-{digest.id}",
            )
        except PermanentError:
            logger.warning("ai_digest_submit_rejected", extra={"digest_id": str(digest.id)})
            with self._uow:
                self._finish(digest, digest.mark_failed("submit_rejected"))
            return
        with self._uow:
            version = digest.version
            digest.mark_submitted(state.job_id)
            self._repo.save(digest, expected_version=version)
            self._scheduler.schedule_poll(
                tenant_slug=self._tenant_slug, digest_id=digest.id, delay_seconds=2.0
            )

    def poll(self, digest_id: UUID) -> None:
        digest = self._repo.get(digest_id)
        if digest is None or digest.status is not InsightStatus.SUBMITTED or not digest.ai_job_id:
            return
        try:
            state = self._client.fetch(
                tenant_slug=self._tenant_slug,
                subject=f"user:{digest.requested_by}",
                job_id=digest.ai_job_id,
            )
        except PermanentError:
            with self._uow:
                self._finish(digest, digest.mark_failed("fetch_rejected"))
            return
        if not state.is_terminal:
            age = (datetime.now(UTC) - digest.created_at).total_seconds()
            if age > self._deadline:
                with self._uow:
                    self._finish(digest, digest.mark_failed("timeout"))
                return
            self._scheduler.schedule_poll(
                tenant_slug=self._tenant_slug, digest_id=digest.id, delay_seconds=poll_delay(age)
            )
            return
        with self._uow:
            if state.status == "succeeded" and state.output is not None:
                generator = state.output.get("generator")
                try:
                    event = digest.mark_succeeded(
                        result=state.output,
                        schema_id=state.schema_id or "",
                        provider=state.provider or "unknown",
                        model=state.model or "unknown",
                        is_mock=not isinstance(generator, dict)
                        or generator.get("is_mock") is not False,
                    )
                except DomainValidationError:
                    event = digest.mark_failed("unsupported_result")
            else:
                event = digest.mark_failed(state.error_code or state.status)
            self._finish(digest, event)

    # --- helpers ----------------------------------------------------------------

    def _create(
        self,
        principal: Principal,
        trigger: DigestTrigger,
        language: str,
        *,
        snapshot: FindingsSnapshot | None = None,
    ) -> tuple[OperationsDigest, bool]:
        snap = snapshot or self._findings.snapshot_for(principal)
        digest = OperationsDigest.request(
            requested_by=principal.user_id,
            trigger=trigger,
            language=language,
            thresholds=snap.thresholds,
            counts=snap.counts,
            findings=snap.findings,
        )
        with self._uow:
            existing = self._repo.find_reusable(principal.user_id, digest.payload_hash)
            if existing is not None and trigger is DigestTrigger.MANUAL:
                return existing, False
            self._repo.add(digest)
            self._record(principal.user_id, f"operations_digest.{trigger.value}_requested", digest)
            self._scheduler.schedule_submit(tenant_slug=self._tenant_slug, digest_id=digest.id)
        return digest, True

    def _finish(self, digest: OperationsDigest, event: DomainEvent) -> None:
        self._repo.save(digest, expected_version=digest.version)
        self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(digest.id))
        self._record(digest.requested_by, f"operations_digest.{digest.status.value}", digest)

    def _record(self, actor: UUID, action: str, digest: OperationsDigest) -> None:
        self._audit.record(
            AuditEntry(
                actor_user_id=actor,
                action=action,
                resource_type=AGGREGATE,
                resource_id=str(digest.id),
            )
        )
