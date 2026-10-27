from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from modules.meetings.domain.meeting import Meeting, MeetingStatus, SubjectType


@dataclass(frozen=True, slots=True)
class MeetingVisibility:
    owner_user_id: UUID | None = None
    team_ids: frozenset[UUID] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class MeetingFilter:
    status: MeetingStatus | None = None
    subject_type: SubjectType | None = None
    subject_id: UUID | None = None
    starts_after: datetime | None = None
    starts_before: datetime | None = None


class MeetingRepository(Protocol):
    def add(self, meeting: Meeting) -> None: ...

    def get(self, meeting_id: UUID) -> Meeting | None: ...

    def save(self, meeting: Meeting, expected_version: int) -> None:
        """Optimistic concurrency: ConflictError if the stored version differs."""
        ...

    def list_visible(
        self, visibility: MeetingVisibility, filters: MeetingFilter, *, limit: int, offset: int
    ) -> list[Meeting]: ...

    def count(self, visibility: MeetingVisibility, filters: MeetingFilter) -> int: ...


class SubjectChecker(Protocol):
    """Verifies the customer or deal a meeting is linked to exists in this tenant."""

    def exists(self, subject_type: SubjectType, subject_id: UUID) -> bool: ...
