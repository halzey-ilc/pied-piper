"""The assistant's tools: thin adapters over other modules' public services.

Every tool runs as the confirming user through the owning module's own use
case, so that module's RBAC, validation, events and audit apply exactly as if
the user had done it by hand. The assistant has no other way to change data:
no SQL, no shell, no direct model writes.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from uuid import UUID

from modules.access_control.domain.rbac import Principal
from modules.activities.application.service import LogNote, LogTask
from modules.activities.composition import activity_service
from modules.activities.domain.activity import SubjectRef, SubjectType
from modules.ai_insights.domain.agent import AgentAction, Subject, Tool
from modules.customers.composition import customer_service
from modules.sales_pipeline.composition import deal_service
from shared.domain.errors import DomainValidationError


class CrmSubjectReader:
    def deal(self, principal: Principal, deal_id: UUID) -> Subject:
        deal = deal_service().get(principal, deal_id)  # NotFoundError if not visible
        return Subject(ref=f"deal:{deal.id}", kind="deal", title=deal.title)

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


class CrmToolExecutor:
    def __init__(self, request_brief: Callable[[Principal, UUID], UUID]) -> None:
        # Starts a deal brief and returns its id (wired by the composition root).
        self._request_brief = request_brief

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
        raise DomainValidationError("Unknown tool.")
