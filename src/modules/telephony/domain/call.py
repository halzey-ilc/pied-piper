"""Call aggregate. Pure Python: no Django, no ORM.

A call is either logged after the fact (``log``) or started as click-to-call
(``request``) and then advanced as the provider reports progress:

    REQUESTED -> RINGING -> IN_PROGRESS -> COMPLETED
          \\----------------> (MISSED | FAILED)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID, uuid4

from shared.domain.errors import ConflictError, DomainValidationError
from shared.domain.events import DomainEvent
from shared.domain.value_objects import PhoneNumber

MAX_DURATION_SECONDS = 24 * 60 * 60
MAX_NOTES_LENGTH = 2000
MAX_URL_LENGTH = 500


class CallDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class CallStatus(StrEnum):
    REQUESTED = "requested"
    RINGING = "ringing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    MISSED = "missed"
    FAILED = "failed"


class SubjectType(StrEnum):
    CUSTOMER = "customer"
    DEAL = "deal"


_TERMINAL = (CallStatus.COMPLETED, CallStatus.MISSED, CallStatus.FAILED)
_ADVANCE_ORDER = {
    CallStatus.REQUESTED: 0,
    CallStatus.RINGING: 1,
    CallStatus.IN_PROGRESS: 2,
}


class CallStateError(ConflictError):
    code = "call_invalid_state"


@dataclass(frozen=True, slots=True)
class CallLink:
    type: SubjectType
    id: UUID


def _clean(text: str | None, *, label: str, max_length: int) -> str | None:
    if not text:
        return None
    cleaned = text.strip()
    if len(cleaned) > max_length:
        raise DomainValidationError(f"{label} must be at most {max_length} characters.")
    return cleaned


@dataclass(frozen=True, kw_only=True)
class CallStarted(DomainEvent):
    event_type: ClassVar[str] = "telephony.call_started"
    call_id: UUID
    direction: str

    def payload(self) -> dict[str, Any]:
        return {"call_id": str(self.call_id), "direction": self.direction}


@dataclass(frozen=True, kw_only=True)
class CallEnded(DomainEvent):
    event_type: ClassVar[str] = "telephony.call_ended"
    call_id: UUID
    status: str
    duration_seconds: int

    def payload(self) -> dict[str, Any]:
        return {
            "call_id": str(self.call_id),
            "status": self.status,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(slots=True)
class Call:
    id: UUID
    direction: CallDirection
    from_number: PhoneNumber
    to_number: PhoneNumber
    owner_user_id: UUID
    status: CallStatus
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: int = 0
    recording_url: str | None = None
    external_id: str | None = None
    link: CallLink | None = None
    team_id: UUID | None = None
    notes: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    version: int = 1

    @classmethod
    def request(
        cls,
        *,
        from_number: str,
        to_number: str,
        owner_user_id: UUID,
        link: CallLink | None = None,
        team_id: UUID | None = None,
    ) -> tuple[Call, CallStarted]:
        call = cls(
            id=uuid4(),
            direction=CallDirection.OUTBOUND,
            from_number=PhoneNumber(from_number),
            to_number=PhoneNumber(to_number),
            owner_user_id=owner_user_id,
            status=CallStatus.REQUESTED,
            link=link,
            team_id=team_id,
        )
        return call, CallStarted(call_id=call.id, direction=call.direction.value)

    @classmethod
    def log(
        cls,
        *,
        direction: CallDirection,
        from_number: str,
        to_number: str,
        owner_user_id: UUID,
        status: CallStatus,
        started_at: datetime,
        duration_seconds: int = 0,
        recording_url: str | None = None,
        external_id: str | None = None,
        link: CallLink | None = None,
        team_id: UUID | None = None,
        notes: str | None = None,
    ) -> tuple[Call, CallEnded]:
        if status not in _TERMINAL:
            raise DomainValidationError("A logged call must be completed, missed or failed.")
        if not 0 <= duration_seconds <= MAX_DURATION_SECONDS:
            raise DomainValidationError("Call duration is out of range.")
        call = cls(
            id=uuid4(),
            direction=direction,
            from_number=PhoneNumber(from_number),
            to_number=PhoneNumber(to_number),
            owner_user_id=owner_user_id,
            status=status,
            started_at=started_at,
            ended_at=started_at,
            duration_seconds=duration_seconds,
            recording_url=_clean(recording_url, label="Recording URL", max_length=MAX_URL_LENGTH),
            external_id=_clean(external_id, label="External id", max_length=255),
            link=link,
            team_id=team_id,
            notes=_clean(notes, label="Notes", max_length=MAX_NOTES_LENGTH),
        )
        return call, CallEnded(
            call_id=call.id, status=status.value, duration_seconds=duration_seconds
        )

    def set_external_id(self, external_id: str) -> None:
        self.external_id = _clean(external_id, label="External id", max_length=255)

    def advance(self, status: CallStatus) -> None:
        """Move a click-to-call forward through ringing/in-progress (not terminal)."""
        if self.status in _TERMINAL:
            raise CallStateError("The call has already ended.")
        if status in _TERMINAL:
            raise CallStateError("Use end() to finish a call.")
        if _ADVANCE_ORDER.get(status, -1) <= _ADVANCE_ORDER.get(self.status, -1):
            raise CallStateError(f"Cannot move from {self.status.value} to {status.value}.")
        if status is CallStatus.IN_PROGRESS:
            self.started_at = datetime.now(UTC)
        self.status = status
        self.version += 1

    def end(
        self, status: CallStatus, *, duration_seconds: int = 0, recording_url: str | None = None
    ) -> CallEnded:
        if self.status in _TERMINAL:
            raise CallStateError("The call has already ended.")
        if status not in _TERMINAL:
            raise DomainValidationError("End status must be completed, missed or failed.")
        if not 0 <= duration_seconds <= MAX_DURATION_SECONDS:
            raise DomainValidationError("Call duration is out of range.")
        self.status = status
        self.ended_at = datetime.now(UTC)
        self.duration_seconds = duration_seconds
        if recording_url:
            self.recording_url = _clean(
                recording_url, label="Recording URL", max_length=MAX_URL_LENGTH
            )
        self.version += 1
        return CallEnded(call_id=self.id, status=status.value, duration_seconds=duration_seconds)
