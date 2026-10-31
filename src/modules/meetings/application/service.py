"""Meeting use cases. Authorization (RBAC + ownership scope) is enforced here for
every operation, independently of the HTTP layer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from modules.access_control.domain.rbac import (
    AccessPolicy,
    Permission,
    Principal,
    ResourceOwnership,
    Scope,
)
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.meetings.application.ports import (
    MeetingFilter,
    MeetingRepository,
    MeetingVisibility,
    SubjectChecker,
)
from modules.meetings.domain.meeting import (
    Meeting,
    MeetingLink,
    Participant,
    ParticipantResponse,
)
from platform_core.application.ports import EventOutbox
from shared.application.ports import UnitOfWork
from shared.domain.errors import ConflictError, NotFoundError
from shared.domain.events import DomainEvent

AGGREGATE = "meeting"
_EventAction = Callable[["Meeting"], "DomainEvent"]


@dataclass(frozen=True, slots=True)
class ScheduleMeeting:
    title: str
    scheduled_start: datetime
    scheduled_end: datetime
    description: str | None = None
    location: str | None = None
    link: MeetingLink | None = None
    participants: tuple[Participant, ...] = ()
    reminders: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class EditMeeting:
    meeting_id: UUID
    expected_version: int | None = None
    title: str | None = None
    description: str | None = None
    location: str | None = None
    set_description: bool = False
    set_location: bool = False
    reminders: tuple[int, ...] | None = None


@dataclass(frozen=True, slots=True)
class MeetingPage:
    items: list[Meeting]
    total: int


def _ownership(meeting: Meeting) -> ResourceOwnership:
    return ResourceOwnership(owner_user_id=meeting.owner_user_id, team_id=meeting.team_id)


class MeetingService:
    def __init__(
        self,
        repository: MeetingRepository,
        subjects: SubjectChecker,
        uow: UnitOfWork,
        outbox: EventOutbox,
        audit: AuditLog,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._repo = repository
        self._subjects = subjects
        self._uow = uow
        self._outbox = outbox
        self._audit = audit
        self._policy = policy or AccessPolicy()

    # --- reads --------------------------------------------------------------

    def get(self, principal: Principal, meeting_id: UUID) -> Meeting:
        meeting = self._repo.get(meeting_id)
        if meeting is None or not self._policy.allows(
            principal, Permission.MEETINGS_VIEW, _ownership(meeting)
        ):
            raise NotFoundError("Meeting not found.")
        return meeting

    def list_meetings(
        self, principal: Principal, filters: MeetingFilter, *, limit: int, offset: int
    ) -> MeetingPage:
        scope = self._policy.require(principal, Permission.MEETINGS_VIEW)
        visibility = self._visibility(principal, scope)
        return MeetingPage(
            items=self._repo.list_visible(visibility, filters, limit=limit, offset=offset),
            total=self._repo.count(visibility, filters),
        )

    def _visibility(self, principal: Principal, scope: Scope) -> MeetingVisibility:
        if scope is Scope.ALL:
            return MeetingVisibility()
        if scope is Scope.TEAM:
            return MeetingVisibility(owner_user_id=principal.user_id, team_ids=principal.team_ids)
        return MeetingVisibility(owner_user_id=principal.user_id)

    # --- writes -------------------------------------------------------------

    def schedule(self, principal: Principal, command: ScheduleMeeting) -> Meeting:
        self._policy.require(principal, Permission.MEETINGS_CREATE)
        with self._uow:
            if command.link is not None and not self._subjects.exists(
                command.link.type, command.link.id
            ):
                raise NotFoundError(
                    f"The {command.link.type.value} this meeting links to was not found."
                )
            meeting, event = Meeting.schedule(
                title=command.title,
                owner_user_id=principal.user_id,
                team_id=principal.department_id,
                scheduled_start=command.scheduled_start,
                scheduled_end=command.scheduled_end,
                description=command.description,
                location=command.location,
                link=command.link,
                participants=command.participants,
                reminders=command.reminders,
            )
            self._repo.add(meeting)
            self._emit(event, meeting)
            self._record(principal, "meeting.scheduled", meeting)
        return meeting

    def edit(self, principal: Principal, command: EditMeeting) -> Meeting:
        with self._uow:
            meeting = self._load_for_write(principal, command.meeting_id)
            self._check_version(meeting, command.expected_version)
            version = meeting.version
            if meeting.edit_details(
                title=command.title,
                description=command.description,
                location=command.location,
                set_description=command.set_description,
                set_location=command.set_location,
                reminders=command.reminders,
            ):
                self._repo.save(meeting, expected_version=version)
                self._record(principal, "meeting.updated", meeting)
        return meeting

    def reschedule(
        self, principal: Principal, meeting_id: UUID, start: datetime, end: datetime
    ) -> Meeting:
        return self._mutate(
            principal, meeting_id, lambda m: m.reschedule(start, end), "meeting.rescheduled"
        )

    def complete(self, principal: Principal, meeting_id: UUID, outcome: str | None) -> Meeting:
        return self._mutate(
            principal, meeting_id, lambda m: m.complete(outcome), "meeting.completed"
        )

    def cancel(self, principal: Principal, meeting_id: UUID, reason: str) -> Meeting:
        return self._mutate(principal, meeting_id, lambda m: m.cancel(reason), "meeting.cancelled")

    def add_participant(
        self, principal: Principal, meeting_id: UUID, participant: Participant
    ) -> Meeting:
        with self._uow:
            meeting = self._load_for_write(principal, meeting_id)
            version = meeting.version
            meeting.add_participant(participant)
            self._repo.save(meeting, expected_version=version)
            self._record(principal, "meeting.participant_added", meeting)
        return meeting

    def set_response(
        self, principal: Principal, meeting_id: UUID, key: str, response: ParticipantResponse
    ) -> Meeting:
        with self._uow:
            meeting = self._load_for_write(principal, meeting_id)
            version = meeting.version
            meeting.set_response(key, response)
            self._repo.save(meeting, expected_version=version)
            self._record(principal, "meeting.participant_responded", meeting)
        return meeting

    # --- helpers ------------------------------------------------------------

    def _mutate(
        self,
        principal: Principal,
        meeting_id: UUID,
        action: _EventAction,
        audit_action: str,
    ) -> Meeting:
        with self._uow:
            meeting = self._load_for_write(principal, meeting_id)
            version = meeting.version
            event = action(meeting)
            self._repo.save(meeting, expected_version=version)
            self._emit(event, meeting)
            self._record(principal, audit_action, meeting)
        return meeting

    def _load_for_write(self, principal: Principal, meeting_id: UUID) -> Meeting:
        meeting = self.get(principal, meeting_id)  # 404 if not visible
        self._policy.require(principal, Permission.MEETINGS_UPDATE, _ownership(meeting))
        return meeting

    def _check_version(self, meeting: Meeting, expected_version: int | None) -> None:
        if expected_version is not None and expected_version != meeting.version:
            raise ConflictError("Meeting was modified by someone else; reload and retry.")

    def _emit(self, event: DomainEvent, meeting: Meeting) -> None:
        self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(meeting.id))

    def _record(self, principal: Principal, action: str, meeting: Meeting) -> None:
        self._audit.record(
            AuditEntry(
                actor_user_id=principal.user_id,
                action=action,
                resource_type=AGGREGATE,
                resource_id=str(meeting.id),
            )
        )
