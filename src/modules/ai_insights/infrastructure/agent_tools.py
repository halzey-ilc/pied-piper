"""The assistant's tools: thin adapters over other modules' public services.

Every tool runs as the confirming user through the owning module's own use
case, so that module's RBAC, validation, events and audit apply exactly as if
the user had done it by hand. The assistant has no other way to change data:
no SQL, no shell, no direct model writes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from modules.access_control.domain.rbac import Principal
from modules.activities.application.service import LogNote, LogTask
from modules.activities.composition import activity_service
from modules.activities.domain.activity import SubjectRef, SubjectType
from modules.ai_insights.domain.agent import AgentAction, Subject, Tool
from modules.customers.composition import customer_service
from modules.meetings.application.service import ScheduleMeeting
from modules.meetings.composition import meeting_service
from modules.meetings.domain.meeting import MeetingLink
from modules.meetings.domain.meeting import SubjectType as MeetingSubject
from modules.sales_pipeline.application.ports import DealFilter
from modules.sales_pipeline.composition import deal_service, pipeline_service
from modules.sales_pipeline.domain.deal import Deal, DealStatus
from modules.sales_pipeline.domain.pipeline import Stage, StageKind, SystemStage
from shared.domain.errors import DomainError, DomainValidationError

# Enough to tell one match from several.
_NAME_MATCHES = 10


def _movable(stage: Stage) -> bool:
    """Stages the assistant may offer. A won stage needs a payment, a lost one a reason
    and «Встреча перенесена» a new time: those stay on the deal card."""
    return (
        stage.is_active
        and stage.kind is StageKind.OPEN
        and stage.system_code is not SystemStage.MEETING_RESCHEDULED
    )


def _deal_subject(principal: Principal, deal: Deal) -> Subject:
    subject = Subject(ref=f"deal:{deal.id}", kind="deal", title=deal.title)
    try:
        pipeline = pipeline_service().get(principal, deal.pipeline_id)
    except DomainError:
        return subject  # no access to the pipeline: the deal simply cannot be moved here
    current = next((s for s in pipeline.stages if s.id == deal.stage_id), None)
    stages = tuple(s.name for s in pipeline.stages if _movable(s) and s.id != deal.stage_id)
    if current is None or deal.status is not DealStatus.OPEN:
        return subject
    return replace(subject, stage=current.name, stages=stages)


class CrmSubjectReader:
    def deal(self, principal: Principal, deal_id: UUID) -> Subject:
        # NotFoundError if not visible
        return _deal_subject(principal, deal_service().get(principal, deal_id))

    def deals_named(self, principal: Principal, names: list[str]) -> list[Subject]:
        for name in names:
            # The deals service applies the principal's own visibility.
            found = (
                deal_service()
                .list_deals(principal, DealFilter(search=name), limit=_NAME_MATCHES, offset=0)
                .items
            )
            if not found:
                continue
            exact = [d for d in found if d.title.casefold() == name.casefold()]
            open_deals = [d for d in found if d.status is DealStatus.OPEN]
            return [_deal_subject(principal, d) for d in exact or open_deals or found]
        return []

    def customer(self, principal: Principal, customer_id: UUID) -> Subject:
        customer = customer_service().get(principal, customer_id)
        # The customer's name is not sent to the AI; the record is referred to by kind.
        return Subject(ref=f"customer:{customer.id}", kind="customer", title="customer")


def _subject_ref(subject: Subject) -> SubjectRef:
    return SubjectRef(type=SubjectType(subject.kind), id=subject.id)


def _due(value: object) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise DomainValidationError("The proposed due date is not a date.") from exc


def _meeting(action: AgentAction, subject: Subject, tz: ZoneInfo) -> ScheduleMeeting:
    try:
        # The AI Platform proposes company-local time without an offset.
        start = datetime.fromisoformat(str(action.args.get("start"))).replace(tzinfo=tz)
        minutes = int(action.args.get("duration_minutes", 60))
    except ValueError as exc:
        raise DomainValidationError("The proposed meeting time is not a time.") from exc
    return ScheduleMeeting(
        title=str(action.args.get("title", "")),
        scheduled_start=start,
        scheduled_end=start + timedelta(minutes=minutes),
        link=MeetingLink(type=MeetingSubject(subject.kind), id=subject.id),
    )


def _move_stage(principal: Principal, action: AgentAction, subject: Subject) -> str:
    if subject.kind != "deal":
        raise DomainValidationError("Only a deal can be moved to a stage.")
    deal = deal_service().get(principal, subject.id)
    pipeline = pipeline_service().get(principal, deal.pipeline_id)
    name = str(action.args.get("stage", ""))
    stage = next((s for s in pipeline.stages if s.name == name and _movable(s)), None)
    if stage is None:
        raise DomainValidationError("That stage is no longer available for this deal.")
    deal_service().move_stage(principal, deal.id, stage.id)
    return f"deal:{deal.id}"


class CrmToolExecutor:
    def __init__(
        self, request_brief: Callable[[Principal, UUID], UUID], *, timezone_name: str = "UTC"
    ) -> None:
        # Starts a deal brief and returns its id (wired by the composition root).
        self._request_brief = request_brief
        self._tz = ZoneInfo(timezone_name)

    def execute(self, principal: Principal, action: AgentAction, subject: Subject) -> str:
        if action.tool is Tool.CREATE_TASK:
            task = activity_service().log_task(
                principal,
                LogTask(
                    subject=_subject_ref(subject),
                    title=str(action.args.get("title", "")),
                    due_date=_due(action.args.get("due_date")),
                ),
            )
            return f"activity:{task.id}"
        if action.tool is Tool.LOG_NOTE:
            note = activity_service().log_note(
                principal,
                LogNote(subject=_subject_ref(subject), body=str(action.args.get("body", ""))),
            )
            return f"activity:{note.id}"
        if action.tool is Tool.REQUEST_DEAL_BRIEF:
            if subject.kind != "deal":
                raise DomainValidationError("A brief can only be prepared for a deal.")
            return f"deal_brief:{self._request_brief(principal, subject.id)}"
        if action.tool is Tool.SCHEDULE_MEETING:
            meeting = meeting_service().schedule(principal, _meeting(action, subject, self._tz))
            return f"meeting:{meeting.id}"
        if action.tool is Tool.MOVE_DEAL_STAGE:
            return _move_stage(principal, action, subject)
        raise DomainValidationError("Unknown tool.")
