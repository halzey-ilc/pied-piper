"""The CRM assistant (AI Agent Runtime): requests become proposed actions, and
actions run only when the user confirms them.

Safety rules, enforced here and not by the AI:
* the AI sees only the tools this user may use and the records they named;
* proposed actions are re-checked against that offer when the plan arrives;
* an action runs only on the requester's explicit confirmation, once, through
  the owning module's service with the requester's own rights (so RBAC, tenant
  isolation and validation are those of the module, not of the assistant);
* every decision is audited.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal
from modules.ai_insights.application.agent_ports import (
    AgentRunRepository,
    AgentScheduler,
    SubjectReader,
    ToolExecutor,
)
from modules.ai_insights.application.ports import AiPlatformClient
from modules.ai_insights.application.service import AiDisabledError, poll_delay
from modules.ai_insights.domain.agent import (
    JOB_TYPE,
    ActionStateError,
    ActionStatus,
    AgentAction,
    AgentRun,
    Subject,
    Tool,
    build_payload,
)
from modules.ai_insights.domain.insight import InsightStatus
from modules.ai_insights.domain.report import validate_language
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from platform_core.application.ports import EventOutbox
from shared.application.ports import UnitOfWork
from shared.application.retry import PermanentError
from shared.domain.errors import DomainError, DomainValidationError, NotFoundError
from shared.domain.events import DomainEvent

logger = logging.getLogger(__name__)

AGGREGATE = "agent_run"

# What a user needs to be offered a tool (the module checks again on execution).
TOOL_PERMISSIONS: dict[Tool, tuple[Permission, ...]] = {
    Tool.CREATE_TASK: (Permission.ACTIVITIES_CREATE,),
    Tool.LOG_NOTE: (Permission.ACTIVITIES_CREATE,),
    Tool.REQUEST_DEAL_BRIEF: (Permission.AI_REQUEST, Permission.DEALS_VIEW),
}


class AgentService:
    def __init__(
        self,
        repository: AgentRunRepository,
        subjects: SubjectReader,
        executor: ToolExecutor,
        client: AiPlatformClient,
        scheduler: AgentScheduler,
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
        self._subjects = subjects
        self._executor = executor
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

    # --- request ----------------------------------------------------------------

    def tools_for(self, principal: Principal) -> list[Tool]:
        return [
            tool
            for tool, needed in TOOL_PERMISSIONS.items()
            if all(self._policy.scope_for(principal, p) is not None for p in needed)
        ]

    def request_run(
        self,
        principal: Principal,
        request: str,
        *,
        deal_id: UUID | None = None,
        customer_id: UUID | None = None,
        language: str | None = None,
    ) -> AgentRun:
        if not self._enabled:
            raise AiDisabledError("AI features are not enabled for this installation.")
        self._policy.require(principal, Permission.AI_REQUEST)
        subjects: list[Subject] = []
        if deal_id is not None:
            subjects.append(self._subjects.deal(principal, deal_id))
        if customer_id is not None:
            subjects.append(self._subjects.customer(principal, customer_id))
        payload = build_payload(
            language=validate_language(language or self._language),
            today=datetime.now(UTC).date().isoformat(),
            request=request,
            tools=self.tools_for(principal),
            subjects=subjects,
        )
        run = AgentRun.request_plan(requested_by=principal.user_id, payload=payload)
        with self._uow:
            self._repo.add(run)
            self._record(principal.user_id, "agent_run.requested", run)
            self._scheduler.schedule_submit(tenant_slug=self._tenant_slug, run_id=run.id)
        return run

    def get(self, principal: Principal, run_id: UUID) -> AgentRun:
        """Runs are personal: only the requester can read or act on one."""
        run = self._repo.get(run_id)
        if run is None or run.requested_by != principal.user_id:
            raise NotFoundError("Assistant run not found.")
        return run

    # --- decisions ----------------------------------------------------------------

    def confirm(self, principal: Principal, run_id: UUID, index: int) -> AgentAction:
        """Execute one proposed action as the requester. Confirming again returns
        the earlier outcome without running it twice."""
        with self._uow:
            run = self.get(principal, run_id)
            action = run.action(index)
            if action.status is not ActionStatus.PROPOSED:
                return action
            version = run.version
            if action.tool not in self.tools_for(principal):
                # The user's role changed since the proposal.
                action.status, action.error_code = ActionStatus.FAILED, "not_allowed"
            else:
                try:
                    action.result_ref = self._executor.execute(
                        principal, action, run.subject(action.subject_ref)
                    )
                    action.status = ActionStatus.EXECUTED
                except DomainError as exc:
                    action.status = ActionStatus.FAILED
                    action.error_code = exc.code[:64]
            action.decided_at = datetime.now(UTC)
            self._repo.save(run, expected_version=version)
            self._audit.record(
                AuditEntry(
                    actor_user_id=principal.user_id,
                    action=f"agent_action.{action.status.value}",
                    resource_type=AGGREGATE,
                    resource_id=str(run.id),
                    changed_fields=(action.tool.value, str(index)),
                )
            )
        return action

    def reject(self, principal: Principal, run_id: UUID, index: int) -> AgentAction:
        with self._uow:
            run = self.get(principal, run_id)
            action = run.action(index)
            if action.status is ActionStatus.REJECTED:
                return action
            if action.status is not ActionStatus.PROPOSED:
                raise ActionStateError("This action has already been decided.")
            version = run.version
            action.status, action.decided_at = ActionStatus.REJECTED, datetime.now(UTC)
            self._repo.save(run, expected_version=version)
            self._audit.record(
                AuditEntry(
                    actor_user_id=principal.user_id,
                    action="agent_action.rejected",
                    resource_type=AGGREGATE,
                    resource_id=str(run.id),
                    changed_fields=(action.tool.value, str(index)),
                )
            )
        return action

    # --- worker steps (idempotent) ------------------------------------------------

    def submit(self, run_id: UUID) -> None:
        run = self._repo.get(run_id)
        if run is None or run.status is not InsightStatus.PENDING:
            return
        try:
            state = self._client.submit(
                tenant_slug=self._tenant_slug,
                subject=f"user:{run.requested_by}",
                job_type=JOB_TYPE,
                payload=run.payload,
                idempotency_key=f"crm-agent-{run.id}",
            )
        except PermanentError:
            logger.warning("ai_agent_submit_rejected", extra={"run_id": str(run.id)})
            with self._uow:
                self._finish(run, run.mark_failed("submit_rejected"))
            return
        with self._uow:
            version = run.version
            run.mark_submitted(state.job_id)
            self._repo.save(run, expected_version=version)
            self._scheduler.schedule_poll(
                tenant_slug=self._tenant_slug, run_id=run.id, delay_seconds=2.0
            )

    def poll(self, run_id: UUID) -> None:
        run = self._repo.get(run_id)
        if run is None or run.status is not InsightStatus.SUBMITTED or not run.ai_job_id:
            return
        try:
            state = self._client.fetch(
                tenant_slug=self._tenant_slug,
                subject=f"user:{run.requested_by}",
                job_id=run.ai_job_id,
            )
        except PermanentError:
            with self._uow:
                self._finish(run, run.mark_failed("fetch_rejected"))
            return
        if not state.is_terminal:
            age = (datetime.now(UTC) - run.created_at).total_seconds()
            if age > self._deadline:
                with self._uow:
                    self._finish(run, run.mark_failed("timeout"))
                return
            self._scheduler.schedule_poll(
                tenant_slug=self._tenant_slug, run_id=run.id, delay_seconds=poll_delay(age)
            )
            return
        with self._uow:
            if state.status == "succeeded" and state.output is not None:
                generator = state.output.get("generator")
                try:
                    event = run.mark_succeeded(
                        result=state.output,
                        schema_id=state.schema_id or "",
                        provider=state.provider or "unknown",
                        model=state.model or "unknown",
                        # Unknown provenance is treated as mock, never as a real model.
                        is_mock=not isinstance(generator, dict)
                        or generator.get("is_mock") is not False,
                    )
                except DomainValidationError:
                    event = run.mark_failed("unsupported_result")
            else:
                event = run.mark_failed(state.error_code or state.status)
            self._finish(run, event)

    # --- helpers ----------------------------------------------------------------

    def _finish(self, run: AgentRun, event: DomainEvent) -> None:
        self._repo.save(run, expected_version=run.version)
        self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(run.id))
        self._record(run.requested_by, f"agent_run.{run.status.value}", run)

    def _record(self, actor: UUID, action: str, run: AgentRun) -> None:
        self._audit.record(
            AuditEntry(
                actor_user_id=actor,
                action=action,
                resource_type=AGGREGATE,
                resource_id=str(run.id),
            )
        )
