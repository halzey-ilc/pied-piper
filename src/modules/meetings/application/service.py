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
from modules.activities.application.next_step import NextStep, NextSteps, ensure_next_step
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.meetings.application.ports import (
    MeetingFilter,
    MeetingRepository,
    MeetingVisibility,
    SubjectChecker,
)
from modules.meetings.domain.meeting import (
    ConfirmationSource,
    Meeting,
    MeetingFormat,
    MeetingLink,
    Participant,
    ParticipantResponse,
)
from platform_core.application.ports import EventOutbox
from shared.application.ports import UnitOfWork
from shared.domain.errors import ConflictError, DomainValidationError, NotFoundError
from shared.domain.events import DomainEvent


class MeetingDateRequiredError(DomainValidationError):
    code = "meeting_date_required"


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
    meeting_format: MeetingFormat = MeetingFormat.OFFICE
    host_user_id: UUID | None = None
    booked_by_user_id: UUID | None = None
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
        next_steps: NextSteps | None = None,
    ) -> None:
        self._next_steps = next_steps
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
            if command.link is not None and not self._subjects.visible_to(
                principal, command.link.type, command.link.id
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
                meeting_format=command.meeting_format,
                host_user_id=command.host_user_id,
                booked_by_user_id=command.booked_by_user_id,
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

    def complete(
        self,
        principal: Principal,
        meeting_id: UUID,
        outcome: str | None,
        *,
        next_step: NextStep | None = None,
    ) -> Meeting:
        with self._uow:
            meeting = self._load_for_write(principal, meeting_id)
            version = meeting.version
            event = meeting.complete(outcome)
            self._plan_next_step(principal, meeting, next_step)
            self._repo.save(meeting, expected_version=version)
            self._emit(event, meeting)
            self._record(principal, "meeting.completed", meeting)
        return meeting

    def cancel(self, principal: Principal, meeting_id: UUID, reason: str) -> Meeting:
        return self._mutate(principal, meeting_id, lambda m: m.cancel(reason), "meeting.cancelled")

    def confirm(
        self,
        principal: Principal,
        meeting_id: UUID,
        source: ConfirmationSource = ConfirmationSource.MANUAL,
    ) -> Meeting:
        with self._uow:
            meeting = self._load_for_write(principal, meeting_id)
            version = meeting.version
            meeting.confirm(by_user_id=principal.user_id, source=source)
            self._repo.save(meeting, expected_version=version)
            self._record(principal, "meeting.confirmed", meeting)
        return meeting

    def record_outcome(
        self,
        principal: Principal,
        meeting_id: UUID,
        outcome: str,
        *,
        new_start: datetime | None = None,
        new_end: datetime | None = None,
        comment: str | None = None,
        reason: str | None = None,
        next_step: NextStep | None = None,
    ) -> Meeting:
        """Record how a scheduled meeting turned out: held / no_show / rescheduled
        (only with the new date and time) / cancelled. ``reason`` — why the client did not
        come (no_show) or why the meeting was cancelled. A held meeting of a deal or
        customer needs a planned next step (``next_step`` creates it) unless one exists."""
        with self._uow:
            meeting = self._load_for_write(principal, meeting_id)
            version = meeting.version
            event: DomainEvent | None = None
            if outcome == "held":
                event = meeting.complete(comment)
                self._plan_next_step(principal, meeting, next_step)
            elif outcome == "no_show":
                event = meeting.mark_no_show(comment, reason)
            elif outcome == "cancelled":
                event = meeting.cancel(reason or comment or "Cancelled.")
            elif outcome == "rescheduled":
                # Owner decision 03.11.2026: a reschedule always carries the new date and time
                if new_start is None or new_end is None:
                    raise MeetingDateRequiredError(
                        "Give the new meeting date and time (new_start and new_end)."
                    )
                event = meeting.reschedule(new_start, new_end)
            else:
                raise DomainValidationError(f"Unknown outcome: {outcome}.")
            self._repo.save(meeting, expected_version=version)
            if event is not None:
                self._emit(event, meeting)
            self._record(principal, f"meeting.outcome_{outcome}", meeting)
        return meeting

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

    def _plan_next_step(
        self, principal: Principal, meeting: Meeting, next_step: NextStep | None
    ) -> None:
        """After a held meeting of a deal or customer the next step must be planned."""
        if self._next_steps is None or meeting.link is None:
            return
        ensure_next_step(
            self._next_steps,
            principal,
            meeting.link.type.value,
            meeting.link.id,
            next_step,
            default_assignee=meeting.owner_user_id,
        )

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
