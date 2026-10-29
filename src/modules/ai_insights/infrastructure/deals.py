"""Reads a deal and its history through the public composition APIs of the
sales_pipeline, activities, meetings and telephony modules.

Every read goes through that module's own service with the requesting user, so
the brief contains only what the user could open in the CRM. A source the user
has no permission for is left out rather than failing the brief. Phone numbers
of calls are never read.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from modules.access_control.domain.rbac import Principal
from modules.activities.application.ports import ActivityFilter
from modules.activities.composition import activity_service
from modules.activities.domain.activity import ActivityKind, SubjectRef, TaskStatus
from modules.activities.domain.activity import SubjectType as ActivitySubject
from modules.ai_insights.application.deal_brief_ports import DealContext
from modules.ai_insights.domain.deal_brief import (
    MAX_HISTORY,
    DealFacts,
    HistoryEntry,
    task_status,
)
from modules.meetings.application.ports import MeetingFilter
from modules.meetings.composition import meeting_service
from modules.meetings.domain.meeting import SubjectType as MeetingSubject
from modules.sales_pipeline.composition import deal_service, pipeline_service
from modules.telephony.application.ports import CallFilter
from modules.telephony.composition import call_service
from modules.telephony.domain.call import SubjectType as CallSubject
from shared.domain.errors import AccessDeniedError


class CrmDealContextReader:
    def read(self, principal: Principal, deal_id: UUID, today: date) -> DealContext:
        deal = deal_service().get(principal, deal_id)  # NotFoundError if not visible
        stages = sorted(
            pipeline_service().get(principal, deal.pipeline_id).stages, key=lambda s: s.position
        )
        stage = next(s for s in stages if s.id == deal.stage_id)
        facts = DealFacts(
            id=deal.id,
            title=deal.title,
            status=deal.status.value,
            stage=stage.name,
            stage_position=stages.index(stage) + 1,
            stage_count=len(stages),
            probability=stage.probability,
            amount=float(deal.value.amount),
            currency=deal.value.currency,
            expected_close_date=deal.expected_close_date,
            created_on=deal.created_at.date(),
        )
        history = self._activities(principal, deal_id, today)
        history += self._meetings(principal, deal_id)
        history += self._calls(principal, deal_id)
        return DealContext(
            facts=facts, owner_user_id=deal.owner_user_id, team_id=deal.team_id, history=history
        )

    def _activities(self, principal: Principal, deal_id: UUID, today: date) -> list[HistoryEntry]:
        try:
            page = activity_service().timeline(
                principal,
                ActivityFilter(subject=SubjectRef(type=ActivitySubject.DEAL, id=deal_id)),
                limit=MAX_HISTORY,
                offset=0,
            )
        except AccessDeniedError:
            return []
        out = []
        for a in page.items:
            status = None
            on = (a.occurred_at or a.created_at).date()
            if a.kind is ActivityKind.TASK:
                on = a.due_date or on
                status = task_status(
                    completed=a.task_status is TaskStatus.COMPLETED, due=a.due_date, today=today
                )
            text = a.body
            if a.kind is ActivityKind.CALL and a.outcome:
                text = f"{a.body}\n{a.outcome}" if a.body else a.outcome
            out.append(
                HistoryEntry(
                    ref=f"activity:{a.id}",
                    kind=a.kind.value,
                    on=on,
                    title=a.title,
                    text=text,
                    status=status,
                )
            )
        return out

    def _meetings(self, principal: Principal, deal_id: UUID) -> list[HistoryEntry]:
        try:
            page = meeting_service().list_meetings(
                principal,
                MeetingFilter(subject_type=MeetingSubject.DEAL, subject_id=deal_id),
                limit=MAX_HISTORY,
                offset=0,
            )
        except AccessDeniedError:
            return []
        return [
            HistoryEntry(
                ref=f"meeting:{m.id}",
                kind="meeting",
                on=m.scheduled_start.date(),
                title=m.title,
                text=m.outcome or m.description,
                status=m.status.value,
            )
            for m in page.items
        ]

    def _calls(self, principal: Principal, deal_id: UUID) -> list[HistoryEntry]:
        try:
            page = call_service().list_calls(
                principal,
                CallFilter(subject_type=CallSubject.DEAL, subject_id=deal_id),
                limit=MAX_HISTORY,
                offset=0,
            )
        except AccessDeniedError:
            return []
        return [
            HistoryEntry(
                ref=f"call:{c.id}",
                kind="call",
                on=(c.started_at or c.created_at).date(),
                title=None,
                text=c.notes,
                status=None,
            )
            for c in page.items
            if c.notes
        ]
