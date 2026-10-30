"""Meeting aggregate with participants and reminders. Pure Python: no Django, no ORM.

Participants and reminders are value objects that belong to the Meeting
aggregate (persisted with it), so the whole meeting stays internally consistent.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID, uuid4

from shared.domain.errors import ConflictError, DomainValidationError
from shared.domain.events import DomainEvent
from shared.domain.value_objects import EmailAddress

MAX_TITLE_LENGTH = 200
MAX_TEXT_LENGTH = 2000
MAX_PARTICIPANTS = 50
MAX_REMINDERS = 10
MAX_DURATION = timedelta(hours=24)
MAX_REMINDER_MINUTES = 4 * 7 * 24 * 60  # four weeks


class MeetingStatus(StrEnum):
    SCHEDULED = "scheduled"
    HELD = "held"
    NO_SHOW = "no_show"
    RESCHEDULED = "rescheduled"  # didn't happen, awaiting a new date
    CANCELLED = "cancelled"
    # Legacy value kept so pre-P0-3 rows still load; new code produces HELD instead.
    COMPLETED = "completed"


class MeetingFormat(StrEnum):
    OFFICE = "office"
    ONLINE = "online"
    VISIT = "visit"


class ConfirmationSource(StrEnum):
    MANUAL = "manual"  # a staff member confirmed after talking to the client
    RESPONSE = "response"  # the client accepted the invitation


class SubjectType(StrEnum):
    CUSTOMER = "customer"
    DEAL = "deal"


class ParticipantResponse(StrEnum):
    NEEDS_ACTION = "needs_action"
    ACCEPTED = "accepted"
    DECLINED = "declined"


class MeetingStateError(ConflictError):
    code = "meeting_invalid_state"


def _clean(text: str, *, label: str, max_length: int, required: bool) -> str | None:
    cleaned = " ".join(text.split()) if text else ""
    if not cleaned:
        if required:
            raise DomainValidationError(f"{label} is required.")
        return None
    if len(cleaned) > max_length:
        raise DomainValidationError(f"{label} must be at most {max_length} characters.")
    return cleaned


@dataclass(frozen=True, slots=True)
class MeetingLink:
    type: SubjectType
    id: UUID


@dataclass(frozen=True, slots=True)
class Participant:
    name: str
    email: str | None = None
    user_id: UUID | None = None
    response: ParticipantResponse = ParticipantResponse.NEEDS_ACTION

    @classmethod
    def create(
        cls,
        *,
        name: str,
        email: str | None = None,
        user_id: UUID | None = None,
    ) -> Participant:
        clean_name = _clean(name, label="Participant name", max_length=120, required=True)
        clean_email = str(EmailAddress(email)) if email else None
        assert clean_name is not None  # noqa: S101  # nosec B101 - _clean(required=True) guarantees it
        return cls(name=clean_name, email=clean_email, user_id=user_id)

    @property
    def key(self) -> str:
        """Identity for deduplication and lookup: e-mail if present, else the name."""
        return (self.email or self.name).lower()


@dataclass(frozen=True, kw_only=True)
class MeetingScheduled(DomainEvent):
    event_type: ClassVar[str] = "meetings.meeting_scheduled"
    meeting_id: UUID
    owner_user_id: UUID
    scheduled_start: datetime

    def payload(self) -> dict[str, Any]:
        return {
            "meeting_id": str(self.meeting_id),
            "owner_user_id": str(self.owner_user_id),
            "scheduled_start": self.scheduled_start.isoformat(),
        }


@dataclass(frozen=True, kw_only=True)
class MeetingRescheduled(DomainEvent):
    event_type: ClassVar[str] = "meetings.meeting_rescheduled"
    meeting_id: UUID
    scheduled_start: datetime

    def payload(self) -> dict[str, Any]:
        return {
            "meeting_id": str(self.meeting_id),
            "scheduled_start": self.scheduled_start.isoformat(),
        }


@dataclass(frozen=True, kw_only=True)
class MeetingCompleted(DomainEvent):
    event_type: ClassVar[str] = "meetings.meeting_completed"
    meeting_id: UUID

    def payload(self) -> dict[str, Any]:
        return {"meeting_id": str(self.meeting_id)}


@dataclass(frozen=True, kw_only=True)
class MeetingCancelled(DomainEvent):
    event_type: ClassVar[str] = "meetings.meeting_cancelled"
    meeting_id: UUID

    def payload(self) -> dict[str, Any]:
        return {"meeting_id": str(self.meeting_id)}


def _validate_window(start: datetime, end: datetime) -> None:
    if start.tzinfo is None or end.tzinfo is None:
        raise DomainValidationError("Meeting times must be timezone-aware.")
    if end <= start:
        raise DomainValidationError("Meeting end must be after its start.")
    if end - start > MAX_DURATION:
        raise DomainValidationError("A meeting must not be longer than 24 hours.")


def _validate_reminders(reminders: tuple[int, ...]) -> tuple[int, ...]:
    if len(reminders) > MAX_REMINDERS:
        raise DomainValidationError(f"A meeting can have at most {MAX_REMINDERS} reminders.")
    for minutes in reminders:
        if not 0 <= minutes <= MAX_REMINDER_MINUTES:
            raise DomainValidationError("Reminder offset is out of range.")
    if len(set(reminders)) != len(reminders):
        raise DomainValidationError("Reminder offsets must be unique.")
    return tuple(sorted(reminders, reverse=True))


def _dedup_participants(participants: tuple[Participant, ...]) -> tuple[Participant, ...]:
    if len(participants) > MAX_PARTICIPANTS:
        raise DomainValidationError(f"A meeting can have at most {MAX_PARTICIPANTS} participants.")
    seen: set[str] = set()
    for participant in participants:
        if participant.key in seen:
            raise DomainValidationError(f"Duplicate participant: {participant.name}.")
        seen.add(participant.key)
    return participants


@dataclass(slots=True)
class Meeting:
    id: UUID
    title: str
    owner_user_id: UUID
    scheduled_start: datetime
    scheduled_end: datetime
    status: MeetingStatus = MeetingStatus.SCHEDULED
    description: str | None = None
    location: str | None = None
    link: MeetingLink | None = None
    team_id: UUID | None = None
    format: MeetingFormat = MeetingFormat.OFFICE
    # Who runs the meeting and who booked it (may differ from the owner).
    host_user_id: UUID | None = None
    booked_by_user_id: UUID | None = None
    confirmed_at: datetime | None = None
    confirmed_by_user_id: UUID | None = None
    confirmation_source: ConfirmationSource | None = None
    participants: tuple[Participant, ...] = ()
    reminders: tuple[int, ...] = ()
    outcome: str | None = None
    cancel_reason: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    version: int = 1

    @classmethod
    def schedule(
        cls,
        *,
        title: str,
        owner_user_id: UUID,
        scheduled_start: datetime,
        scheduled_end: datetime,
        description: str | None = None,
        location: str | None = None,
        link: MeetingLink | None = None,
        team_id: UUID | None = None,
        meeting_format: MeetingFormat = MeetingFormat.OFFICE,
        host_user_id: UUID | None = None,
        booked_by_user_id: UUID | None = None,
        participants: tuple[Participant, ...] = (),
        reminders: tuple[int, ...] = (),
    ) -> tuple[Meeting, MeetingScheduled]:
        _validate_window(scheduled_start, scheduled_end)
        meeting = cls(
            id=uuid4(),
            title=_clean(title, label="Title", max_length=MAX_TITLE_LENGTH, required=True) or "",
            owner_user_id=owner_user_id,
            scheduled_start=scheduled_start,
            scheduled_end=scheduled_end,
            description=_clean(
                description or "", label="Description", max_length=MAX_TEXT_LENGTH, required=False
            ),
            location=_clean(location or "", label="Location", max_length=200, required=False),
            link=link,
            team_id=team_id,
            format=meeting_format,
            # The host runs it and the booker arranged it; both default to the owner.
            host_user_id=host_user_id or owner_user_id,
            booked_by_user_id=booked_by_user_id or owner_user_id,
            participants=_dedup_participants(participants),
            reminders=_validate_reminders(reminders),
        )
        return meeting, MeetingScheduled(
            meeting_id=meeting.id,
            owner_user_id=owner_user_id,
            scheduled_start=scheduled_start,
        )

    @property
    def duration_minutes(self) -> int:
        return int((self.scheduled_end - self.scheduled_start).total_seconds() // 60)

    def _require_scheduled(self, action: str) -> None:
        if self.status is not MeetingStatus.SCHEDULED:
            raise MeetingStateError(f"A {self.status.value} meeting cannot be {action}.")

    def confirm(
        self, *, by_user_id: UUID, source: ConfirmationSource = ConfirmationSource.MANUAL
    ) -> None:
        """Record that the meeting is confirmed (separate from its status)."""
        self._require_scheduled("confirmed")
        self.confirmed_at = datetime.now(UTC)
        self.confirmed_by_user_id = by_user_id
        self.confirmation_source = source
        self.version += 1

    def mark_no_show(self, comment: str | None = None) -> MeetingCompleted:
        self._require_scheduled("marked as a no-show")
        self.status = MeetingStatus.NO_SHOW
        self.outcome = _clean(
            comment or "", label="Outcome", max_length=MAX_TEXT_LENGTH, required=False
        )
        self.version += 1
        return MeetingCompleted(meeting_id=self.id)

    def mark_rescheduled_pending(self, comment: str | None = None) -> None:
        """The meeting did not happen and awaits a new date (no date yet)."""
        self._require_scheduled("rescheduled")
        self.status = MeetingStatus.RESCHEDULED
        self.outcome = _clean(
            comment or "", label="Outcome", max_length=MAX_TEXT_LENGTH, required=False
        )
        self.version += 1

    def edit_details(
        self,
        *,
        title: str | None = None,
        description: str | None = None,
        location: str | None = None,
        set_description: bool = False,
        set_location: bool = False,
        reminders: tuple[int, ...] | None = None,
    ) -> bool:
        self._require_scheduled("edited")
        changed = False
        if title is not None:
            new_title = _clean(title, label="Title", max_length=MAX_TITLE_LENGTH, required=True)
            if new_title != self.title:
                self.title, changed = new_title or self.title, True
        if set_description:
            new_desc = _clean(
                description or "", label="Description", max_length=MAX_TEXT_LENGTH, required=False
            )
            if new_desc != self.description:
                self.description, changed = new_desc, True
        if set_location:
            new_loc = _clean(location or "", label="Location", max_length=200, required=False)
            if new_loc != self.location:
                self.location, changed = new_loc, True
        if reminders is not None:
            new_reminders = _validate_reminders(reminders)
            if new_reminders != self.reminders:
                self.reminders, changed = new_reminders, True
        if changed:
            self.version += 1
        return changed

    def reschedule(self, start: datetime, end: datetime) -> MeetingRescheduled:
        # Allowed while scheduled or awaiting a new date; a new date resets it to
        # scheduled and clears any prior confirmation (it must be re-confirmed).
        if self.status not in (MeetingStatus.SCHEDULED, MeetingStatus.RESCHEDULED):
            raise MeetingStateError(f"A {self.status.value} meeting cannot be rescheduled.")
        _validate_window(start, end)
        self.scheduled_start = start
        self.scheduled_end = end
        self.status = MeetingStatus.SCHEDULED
        self.confirmed_at = None
        self.confirmed_by_user_id = None
        self.confirmation_source = None
        self.version += 1
        return MeetingRescheduled(meeting_id=self.id, scheduled_start=start)

    def add_participant(self, participant: Participant) -> None:
        self._require_scheduled("changed")
        self.participants = _dedup_participants((*self.participants, participant))
        self.version += 1

    def remove_participant(self, key: str) -> None:
        self._require_scheduled("changed")
        remaining = tuple(p for p in self.participants if p.key != key.lower())
        if len(remaining) == len(self.participants):
            raise DomainValidationError("No such participant.")
        self.participants = remaining
        self.version += 1

    def set_response(self, key: str, response: ParticipantResponse) -> None:
        self._require_scheduled("changed")
        updated = tuple(
            replace(p, response=response) if p.key == key.lower() else p for p in self.participants
        )
        if updated == self.participants:
            raise DomainValidationError("No such participant.")
        self.participants = updated
        self.version += 1

    def complete(self, outcome: str | None = None) -> MeetingCompleted:
        """Mark the meeting as held (it took place)."""
        self._require_scheduled("completed")
        self.status = MeetingStatus.HELD
        self.outcome = _clean(
            outcome or "", label="Outcome", max_length=MAX_TEXT_LENGTH, required=False
        )
        self.version += 1
        return MeetingCompleted(meeting_id=self.id)

    def cancel(self, reason: str) -> MeetingCancelled:
        self._require_scheduled("cancelled")
        cleaned = _clean(reason, label="Cancellation reason", max_length=500, required=True)
        self.status = MeetingStatus.CANCELLED
        self.cancel_reason = cleaned
        self.version += 1
        return MeetingCancelled(meeting_id=self.id)
