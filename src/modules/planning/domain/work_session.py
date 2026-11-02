"""Working sessions: a member says when they start and stop working. Pure domain.

A session is NOT the login: signing in proves nothing about work, and closing the
browser is not an end of work. Only an explicit stop closes a session with an end
time and a duration.

Policy for sessions nobody stops (documented in docs/API_CONTRACT.md): a session still
open ``MAX_OPEN`` after its start is marked ``unconfirmed`` — its end is unknown, no end
time is invented, and it does not count towards worked time. Starting a new session
applies the same rule to a stale one first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from shared.domain.errors import ConflictError

MAX_OPEN = timedelta(hours=16)


class WorkSessionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"  # stopped explicitly: end and duration are known
    UNCONFIRMED = "unconfirmed"  # never stopped: end unknown, not counted


class WorkSessionStateError(ConflictError):
    code = "work_session_invalid_state"


@dataclass(slots=True)
class WorkSession:
    id: UUID
    user_id: UUID
    team_id: UUID | None
    started_at: datetime
    status: WorkSessionStatus = WorkSessionStatus.OPEN
    ended_at: datetime | None = None  # only for CLOSED
    settled_at: datetime | None = None  # when it stopped being open (stop or expiry)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def start(cls, *, user_id: UUID, team_id: UUID | None, now: datetime) -> WorkSession:
        return cls(id=uuid4(), user_id=user_id, team_id=team_id, started_at=now)

    @property
    def duration_seconds(self) -> int | None:
        if self.status is not WorkSessionStatus.CLOSED or self.ended_at is None:
            return None
        return int((self.ended_at - self.started_at).total_seconds())

    def is_stale(self, now: datetime) -> bool:
        return self.status is WorkSessionStatus.OPEN and now - self.started_at > MAX_OPEN

    def stop(self, now: datetime) -> None:
        self._require_open()
        if self.is_stale(now):
            raise WorkSessionStateError(
                "The session was open too long to be confirmed; start a new one."
            )
        self.status = WorkSessionStatus.CLOSED
        self.ended_at = now
        self.settled_at = now

    def mark_unconfirmed(self, now: datetime) -> None:
        self._require_open()
        self.status = WorkSessionStatus.UNCONFIRMED
        self.ended_at = None
        self.settled_at = now

    def _require_open(self) -> None:
        if self.status is not WorkSessionStatus.OPEN:
            raise WorkSessionStateError(f"The session is already {self.status.value}.")
