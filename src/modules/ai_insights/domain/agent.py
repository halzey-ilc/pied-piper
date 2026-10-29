"""The AgentRun aggregate: a user's request to the CRM assistant, the actions the
AI proposed, and what the user decided for each (AI Agent Runtime).

The AI never acts. It proposes actions restricted to the tools this user may
use and the records they named; each action runs only when the user confirms
it, through the owning module's service with the user's own rights.

run:    pending -> submitted -> succeeded | failed
action: proposed -> executed | failed | rejected
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID, uuid4

from modules.ai_insights.domain.insight import InsightStatus
from modules.ai_insights.domain.report import payload_fingerprint, validate_language
from shared.domain.errors import ConflictError, DomainValidationError
from shared.domain.events import DomainEvent

JOB_TYPE = "agent.plan.v1"
OUTPUT_SCHEMA = "agent_plan.output.v1"
MAX_REQUEST = 1000
MAX_ACTIONS = 5


class Tool(StrEnum):
    CREATE_TASK = "create_task"
    LOG_NOTE = "log_note"
    REQUEST_DEAL_BRIEF = "request_deal_brief"


class ActionStatus(StrEnum):
    PROPOSED = "proposed"
    EXECUTED = "executed"
    FAILED = "failed"
    REJECTED = "rejected"


class ActionStateError(ConflictError):
    code = "agent_action_invalid_state"


@dataclass(frozen=True, slots=True)
class Subject:
    ref: str  # "deal:<uuid>" | "customer:<uuid>"
    kind: str  # deal | customer
    title: str

    @property
    def id(self) -> UUID:
        return UUID(self.ref.split(":", 1)[1])


@dataclass
class AgentAction:
    index: int
    tool: Tool
    subject_ref: str
    args: dict[str, Any]  # title/due_date for tasks, body for notes
    reason: str
    status: ActionStatus = ActionStatus.PROPOSED
    result_ref: str | None = None  # what was created, e.g. "activity:<uuid>"
    error_code: str | None = None
    decided_at: datetime | None = None


def build_payload(
    *, language: str, today: str, request: str, tools: list[Tool], subjects: list[Subject]
) -> dict[str, Any]:
    text = request.strip()
    if not text:
        raise DomainValidationError("The request must not be empty.")
    if len(text) > MAX_REQUEST:
        raise DomainValidationError(f"The request must be at most {MAX_REQUEST} characters.")
    if not tools:
        raise DomainValidationError("You may not use any assistant tool.")
    return {
        "language": validate_language(language),
        "today": today,
        "request": text,
        "tools": [t.value for t in tools],
        "subjects": [{"ref": s.ref, "kind": s.kind, "title": s.title[:200]} for s in subjects],
    }


def actions_from(result: dict[str, Any], payload: dict[str, Any]) -> list[AgentAction]:
    """The proposed actions, re-checked against what the CRM offered: an action
    with a tool or record that was not offered is dropped, whatever the AI said."""
    offered = set(payload["tools"])
    refs = {s["ref"] for s in payload["subjects"]}
    out: list[AgentAction] = []
    for raw in result.get("actions", [])[:MAX_ACTIONS]:
        tool, ref = raw.get("tool"), raw.get("subject_ref")
        if tool not in offered or ref not in refs:
            continue
        args = {k: raw[k] for k in ("title", "due_date", "body") if k in raw}
        out.append(
            AgentAction(
                index=len(out),
                tool=Tool(tool),
                subject_ref=ref,
                args=args,
                reason=str(raw.get("reason", ""))[:300],
            )
        )
    return out


@dataclass(frozen=True, kw_only=True)
class AgentRunCompleted(DomainEvent):
    event_type: ClassVar[str] = "ai_insights.agent_run_completed"

    run_id: UUID
    requested_by: UUID
    succeeded: bool

    def payload(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id),
            "requested_by": str(self.requested_by),
            "succeeded": self.succeeded,
        }


@dataclass
class AgentRun:
    id: UUID
    requested_by: UUID
    request: str
    payload: dict[str, Any]
    source_hash: str
    status: InsightStatus
    created_at: datetime
    actions: list[AgentAction] = field(default_factory=list)
    ai_job_id: str | None = None
    summary: str | None = None
    provider: str | None = None
    model: str | None = None
    is_mock: bool = False
    error_code: str | None = None
    finished_at: datetime | None = None
    version: int = 1

    @classmethod
    def request_plan(cls, *, requested_by: UUID, payload: dict[str, Any]) -> AgentRun:
        return cls(
            id=uuid4(),
            requested_by=requested_by,
            request=payload["request"],
            payload=payload,
            source_hash=payload_fingerprint(payload),
            status=InsightStatus.PENDING,
            created_at=datetime.now(UTC),
        )

    @property
    def is_finished(self) -> bool:
        return self.status in (InsightStatus.SUCCEEDED, InsightStatus.FAILED)

    def mark_submitted(self, ai_job_id: str) -> None:
        if self.status is not InsightStatus.PENDING or not ai_job_id:
            raise DomainValidationError("Only a pending run can be submitted.")
        self.ai_job_id = ai_job_id
        self.status = InsightStatus.SUBMITTED

    def mark_succeeded(
        self, *, result: dict[str, Any], schema_id: str, provider: str, model: str, is_mock: bool
    ) -> AgentRunCompleted:
        if self.status is not InsightStatus.SUBMITTED:
            raise DomainValidationError("Only a submitted run can succeed.")
        if schema_id != OUTPUT_SCHEMA:
            raise DomainValidationError("The AI result uses an unsupported output contract.")
        self.actions = actions_from(result, self.payload)
        self.summary = str(result.get("summary", ""))[:300]
        self.provider, self.model, self.is_mock = provider, model, is_mock
        self.status = InsightStatus.SUCCEEDED
        self.finished_at = datetime.now(UTC)
        return self._completed(succeeded=True)

    def mark_failed(self, error_code: str) -> AgentRunCompleted:
        if self.is_finished:
            raise DomainValidationError("The run is already finished.")
        self.error_code = error_code[:64]
        self.status = InsightStatus.FAILED
        self.finished_at = datetime.now(UTC)
        return self._completed(succeeded=False)

    def action(self, index: int) -> AgentAction:
        if self.status is not InsightStatus.SUCCEEDED or not 0 <= index < len(self.actions):
            raise ActionStateError("There is no such proposed action.")
        return self.actions[index]

    def subject(self, ref: str) -> Subject:
        for s in self.payload["subjects"]:
            if s["ref"] == ref:
                return Subject(ref=s["ref"], kind=s["kind"], title=s["title"])
        raise ActionStateError("The action targets a record that was not part of the request.")

    def _completed(self, *, succeeded: bool) -> AgentRunCompleted:
        return AgentRunCompleted(
            run_id=self.id, requested_by=self.requested_by, succeeded=succeeded
        )
