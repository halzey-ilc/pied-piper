"""Adapter implementing the DealMeetings port via the meetings context.

Runs inside the deal service's unit of work on the active tenant database: the stage move
and the meeting's new date commit together (or not at all). The deal is already loaded with
the caller's rights, so the meeting service does not look it up again (that lookup would wire
meetings back to deals).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from modules.access_control.domain.rbac import Principal
from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.meetings.application.ports import MeetingFilter
from modules.meetings.application.service import MeetingService, ScheduleMeeting
from modules.meetings.domain.meeting import MeetingLink, MeetingStatus, SubjectType
from modules.meetings.infrastructure.repository import DjangoMeetingRepository
from modules.sales_pipeline.domain.deal import Deal
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from shared.infrastructure.unit_of_work import DjangoUnitOfWork

# A deal has a handful of upcoming meetings at most; this bounds the lookup.
_LOOKUP_LIMIT = 50


class _CheckedByCaller:
    def visible_to(self, principal: Principal, subject_type: SubjectType, subject_id: UUID) -> bool:
        return True


class MeetingsDealMeetings:
    def reschedule_next(
        self, principal: Principal, deal: Deal, start: datetime, end: datetime
    ) -> tuple[UUID, datetime | None]:
        alias = require_tenant().db_alias
        service = MeetingService(
            repository=DjangoMeetingRepository(alias),
            subjects=_CheckedByCaller(),
            uow=DjangoUnitOfWork(alias),
            outbox=DjangoEventOutbox(alias),
            audit=DjangoAuditLog(alias),
        )
        candidates = [
            meeting
            for status in (MeetingStatus.SCHEDULED, MeetingStatus.RESCHEDULED)
            for meeting in service.list_meetings(
                principal,
                MeetingFilter(status=status, subject_type=SubjectType.DEAL, subject_id=deal.id),
                limit=_LOOKUP_LIMIT,
                offset=0,
            ).items
        ]
        if candidates:
            nearest = min(candidates, key=lambda m: m.scheduled_start)
            previous = nearest.scheduled_start
            return service.reschedule(principal, nearest.id, start, end).id, previous
        booked = service.schedule(
            principal,
            ScheduleMeeting(
                title=f"Встреча: {deal.title}"[:200],
                scheduled_start=start,
                scheduled_end=end,
                link=MeetingLink(SubjectType.DEAL, deal.id),
                host_user_id=deal.owner_user_id,
            ),
        )
        return booked.id, None
