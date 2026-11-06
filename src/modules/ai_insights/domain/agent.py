# ruff: noqa: RUF001
"""The AgentRun aggregate: a user's request to the CRM assistant, the actions the
AI proposed, and what the user decided for each (AI Agent Runtime).

The AI never acts. It proposes actions restricted to the tools this user may
use and the records they named; each action runs only when the user confirms
it, through the owning module's service with the user's own rights.

run:    pending -> submitted -> succeeded | failed
action: proposed -> executed | failed | rejected
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID, uuid4

from modules.ai_insights.domain.insight import InsightStatus
from modules.ai_insights.domain.redaction import mask_personal_data, redact_contacts
from modules.ai_insights.domain.report import payload_fingerprint, validate_language
from shared.domain.errors import ConflictError, DomainValidationError
from shared.domain.events import DomainEvent

JOB_TYPE = "agent.plan.v3"
# v1: runs submitted before the assistant moved to v3 can still finish.
OUTPUT_SCHEMAS = frozenset({"agent_plan.output.v1", "agent_plan.output.v2"})
MAX_REQUEST = 1000
MAX_ACTIONS = 5
MAX_HISTORY = 10  # turns sent to the AI Platform (agent_plan.input.v3)
MAX_CHOICES = 5
LOCAL_PROVIDER = "crm"  # answered by the CRM itself, no model involved


class Tool(StrEnum):
    CREATE_TASK = "create_task"
    LOG_NOTE = "log_note"
    REQUEST_DEAL_BRIEF = "request_deal_brief"
    SCHEDULE_MEETING = "schedule_meeting"
    MOVE_DEAL_STAGE = "move_deal_stage"
    # Proposed by the CRM from the request itself, never by the model (agent_commands).
    UPDATE_DEAL_AMOUNT = "update_deal_amount"
    CHANGE_DEAL_OWNER = "change_deal_owner"
    UPDATE_PLAN = "update_plan"
    RECORD_MEETING_OUTCOME = "record_meeting_outcome"


CRM_TOOLS = frozenset(
    {
        Tool.UPDATE_DEAL_AMOUNT,
        Tool.CHANGE_DEAL_OWNER,
        Tool.UPDATE_PLAN,
        Tool.RECORD_MEETING_OUTCOME,
    }
)

_TOOL_NAMES: dict[str, dict[Tool, str]] = {
    "ru": {
        Tool.CREATE_TASK: "поставить задачу",
        Tool.LOG_NOTE: "записать заметку",
        Tool.REQUEST_DEAL_BRIEF: "подготовить бриф",
        Tool.SCHEDULE_MEETING: "назначить встречу",
        Tool.MOVE_DEAL_STAGE: "перенести сделку на этап",
    },
    "ky": {
        Tool.CREATE_TASK: "тапшырма коюу",
        Tool.LOG_NOTE: "эскертме жазуу",
        Tool.REQUEST_DEAL_BRIEF: "бриф даярдоо",
        Tool.SCHEDULE_MEETING: "жолугушуу белгилөө",
        Tool.MOVE_DEAL_STAGE: "бүтүмдү этапка которуу",
    },
    "en": {
        Tool.CREATE_TASK: "create a task",
        Tool.LOG_NOTE: "write a note",
        Tool.REQUEST_DEAL_BRIEF: "prepare a brief",
        Tool.SCHEDULE_MEETING: "schedule a meeting",
        Tool.MOVE_DEAL_STAGE: "move a deal to a stage",
    },
}

_MESSAGES: dict[str, dict[str, str]] = {
    "ru": {
        "confirm": "Проверьте изменение: ничего не изменится, пока вы не подтвердите.",
        "requested": "Вы попросили об этом в запросе.",
        "no_access": "У вашей роли нет права на это действие.",
        "no_plan": "Такого плана на этот период нет. Создайте его в разделе планов.",
        "several_plans": "На этот период несколько планов. Измените нужный в разделе планов.",
        "no_member": "Не нашёл такого сотрудника. Напишите имя и фамилию.",
        "several_members": "Подходит несколько сотрудников. Напишите имя и фамилию.",
        "no_meeting": "У этой сделки нет назначенной встречи.",
        "several_meetings": "У сделки несколько назначенных встреч. Отметьте итог на встрече.",
        "cannot": "Этого я сделать не могу. Я умею: {tools}.",
        "need_deal": "По какой сделке? Напишите её название, например: по сделке Поставка.",
        "no_deal": "Не нашёл сделку «{name}». Проверьте название.",
        "several": "Нашёл несколько сделок по запросу «{name}». Какую вы имели в виду?",
    },
    "ky": {
        "confirm": "Өзгөрүүнү текшериңиз: сиз ырастамайынча эч нерсе өзгөрбөйт.",
        "requested": "Муну суроодо сурадыңыз.",
        "no_access": "Ролуңузда бул аракетке укук жок.",
        "no_plan": "Бул мезгилге мындай план жок.",
        "several_plans": "Бул мезгилге бир нече план бар. Аны пландар бөлүмүндө өзгөртүңүз.",
        "no_member": "Мындай кызматкер табылган жок. Атын жана фамилиясын жазыңыз.",
        "several_members": "Бир нече кызматкер туура келет. Атын жана фамилиясын жазыңыз.",
        "no_meeting": "Бул бүтүмдө белгиленген жолугушуу жок.",
        "several_meetings": "Бүтүмдө бир нече жолугушуу бар. Жыйынтыкты жолугушуудан белгилеңиз.",
        "cannot": "Муну аткара албайм. Мен кыла алам: {tools}.",
        "need_deal": "Кайсы бүтүм боюнча? Анын аталышын жазыңыз.",
        "no_deal": "«{name}» бүтүмү табылган жок. Аталышын текшериңиз.",
        "several": "«{name}» боюнча бир нече бүтүм табылды. Кайсынысы?",
    },
    "en": {
        "confirm": "Check the change: nothing changes until you confirm.",
        "requested": "You asked for this in your request.",
        "no_access": "Your role may not do this.",
        "no_plan": "There is no such plan for this period. Create it in the plans section.",
        "several_plans": "This period has several plans. Change one in the plans section.",
        "no_member": "No such employee was found. Write the first and last name.",
        "several_members": "Several employees match. Write the first and last name.",
        "no_meeting": "This deal has no scheduled meeting.",
        "several_meetings": "This deal has several scheduled meetings. Use the meeting itself.",
        "cannot": "I cannot do that. I can: {tools}.",
        "need_deal": "Which deal? Name it, for example: on the deal Supply.",
        "no_deal": "No deal named “{name}” was found. Check the name.",
        "several": "Several deals match “{name}”. Which one do you mean?",
    },
}


def message(language: str, key: str, **values: str) -> str:
    return _MESSAGES[language][key].format(**values)[:300]


def cannot_do(language: str, tools: list[str]) -> str:
    """Why nothing was proposed, with what the assistant can do for this user."""
    names = _TOOL_NAMES[language]
    return message(language, "cannot", tools=", ".join(names[Tool(t)] for t in tools))


class ActionStatus(StrEnum):
    PROPOSED = "proposed"
    EXECUTED = "executed"
    FAILED = "failed"
    REJECTED = "rejected"


class ActionStateError(ConflictError):
    code = "agent_action_invalid_state"


@dataclass(frozen=True, slots=True)
class Subject:
    ref: str  # "deal:<uuid>" | "customer:<uuid>"; plan: and meeting: in CRM commands
    kind: str  # deal | customer | plan | meeting
    title: str
    stage: str | None = None  # deals: the current stage
    stages: tuple[str, ...] = ()  # deals: the stages it may be moved to

    @property
    def id(self) -> UUID:
        return UUID(self.ref.split(":", 1)[1])


@dataclass
class AgentAction:
    index: int
    tool: Tool
    subject_ref: str
    args: dict[str, Any]  # the tool's fields (see ACTION_ARGS)
    reason: str
    status: ActionStatus = ActionStatus.PROPOSED
    result_ref: str | None = None  # what was created, e.g. "activity:<uuid>"
    error_code: str | None = None
    decided_at: datetime | None = None


ACTION_ARGS = ("title", "due_date", "body", "start", "duration_minutes", "stage")

_QUOTED = re.compile(r"[«\"“]([^»\"”]{2,200})[»\"”]")
_DEAL_WORD = re.compile(r"(?:сделк|бүтүм|келишим|\bdeal\b)\w*\s+([^:;,.!?\n]+)", re.IGNORECASE)
# A word after «сделку» that starts the rest of the sentence, not a name: «сделку на этап…».
_NOT_A_NAME = frozenset({"на", "в", "во", "по", "с", "со", "к", "ко", "до", "для", "и", "to", "on"})
_MAX_NAME_WORDS = 6


def quoted_name(request: str) -> str | None:
    """The record a request names in quotes: «поставь задачу по сделке «Поставка»»."""
    match = _QUOTED.search(request)
    return match.group(1).strip() if match else None


def deal_names(request: str) -> list[str]:
    """What the request may call its deal, the most likely first: the quoted name, or
    the words after «сделка», longest first, since where the name ends is unknown
    («по сделке Тест доски завтра» tries «Тест доски завтра», «Тест доски», «Тест»)."""
    quoted = quoted_name(request)
    if quoted:
        return [quoted]
    match = _DEAL_WORD.search(request)
    if match is None:
        return []
    words = match.group(1).split()[:_MAX_NAME_WORDS]
    if not words or words[0].casefold() in _NOT_A_NAME:
        return []
    return [" ".join(words[:n]) for n in range(len(words), 0, -1)]


def proposed_text(actions: list[AgentAction]) -> str:
    """An assistant turn for agent_plan.input.v3: the tool and its values, which the AI
    Platform uses to tell a repeated proposal from a changed one."""
    if not actions:
        return "Nothing proposed"
    parts = []
    for a in actions:
        values = [str(a.args[k]) for k in ACTION_ARGS if a.args.get(k) is not None]
        parts.append(" ".join([a.tool.value, *values]))
    return f"Proposed: {'; '.join(parts)}"[:MAX_REQUEST]


def question_payload(*, language: str, today: str, request: str) -> dict[str, Any]:
    """What every run stores; a question the CRM answers itself needs no more."""
    text = request.strip()
    if not text:
        raise DomainValidationError("The request must not be empty.")
    if len(text) > MAX_REQUEST:
        raise DomainValidationError(f"The request must be at most {MAX_REQUEST} characters.")
    return {
        "language": validate_language(language),
        "today": today,
        "request": redact_contacts(text),
    }


def build_payload(
    *,
    language: str,
    today: str,
    request: str,
    tools: list[Tool],
    subjects: list[Subject],
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    if not tools:
        raise DomainValidationError("You may not use any assistant tool.")
    payload: dict[str, Any] = {
        **question_payload(language=language, today=today, request=request),
        "tools": [t.value for t in tools],
        "subjects": [subject_json(s) for s in subjects],
    }
    if history:
        payload["history"] = history[-MAX_HISTORY:]
    return payload


def subject_json(s: Subject) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ref": s.ref,
        "kind": s.kind,
        "title": mask_personal_data(s.title)[:200],
    }
    if s.stage:
        out["stage"] = s.stage[:100]
    if s.stages:
        out["stages"] = [name[:100] for name in s.stages]
    return out


def actions_from(result: dict[str, Any], payload: dict[str, Any]) -> list[AgentAction]:
    """The proposed actions, re-checked against what the CRM offered: an action
    with a tool, record or stage that was not offered is dropped, whatever the AI said."""
    offered = set(payload["tools"])
    subjects = {s["ref"]: s for s in payload["subjects"]}
    out: list[AgentAction] = []
    for raw in result.get("actions", [])[:MAX_ACTIONS]:
        tool, ref = raw.get("tool"), raw.get("subject_ref")
        if tool not in offered or ref not in subjects:
            continue
        if tool == Tool.MOVE_DEAL_STAGE and raw.get("stage") not in subjects[ref].get("stages", []):
            continue
        args = {k: raw[k] for k in ACTION_ARGS if k in raw}
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

    @classmethod
    def answer_locally(
        cls,
        *,
        requested_by: UUID,
        payload: dict[str, Any],
        summary: str,
        choices: list[dict[str, str]] | None = None,
        items: list[dict[str, str | None]] | None = None,
        actions: list[AgentAction] | None = None,
    ) -> AgentRun:
        """A run the CRM finishes itself, without the AI Platform. ``choices`` are records
        the user can pick from and ``items`` the lines of a data answer; both stay in the
        CRM."""
        run = cls.request_plan(
            requested_by=requested_by,
            payload={**payload, "choices": choices or [], "items": items or []},
        )
        run.summary = summary[:300]
        run.actions = actions or []
        run.provider, run.model = LOCAL_PROVIDER, "rules"
        run.status = InsightStatus.SUCCEEDED
        run.finished_at = run.created_at
        return run

    @property
    def is_finished(self) -> bool:
        return self.status in (InsightStatus.SUCCEEDED, InsightStatus.FAILED)

    def conversation(self) -> list[dict[str, str]]:
        """The turns up to and including this run, for a request that follows it."""
        turns = [
            *self.payload.get("history", []),
            {"role": "user", "text": str(self.payload["request"])[:MAX_REQUEST]},
            {"role": "assistant", "text": proposed_text(self.actions)},
        ]
        return turns[-MAX_HISTORY:]

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
        if schema_id not in OUTPUT_SCHEMAS:
            raise DomainValidationError("The AI result uses an unsupported output contract.")
        self.actions = actions_from(result, self.payload)
        self.summary = (
            str(result.get("summary", ""))[:300]
            if self.actions
            else cannot_do(self.payload["language"], self.payload["tools"])
        )
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
