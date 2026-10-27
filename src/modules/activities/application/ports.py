from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from modules.activities.domain.activity import (
    Activity,
    ActivityKind,
    SubjectRef,
    SubjectType,
    TaskStatus,
)


@dataclass(frozen=True, slots=True)
class ActivityVisibility:
    """Row filter derived from the caller's RBAC scope. ``None`` owner = all activities."""

    owner_user_id: UUID | None = None
    team_ids: frozenset[UUID] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class ActivityFilter:
    kind: ActivityKind | None = None
    subject: SubjectRef | None = None
    task_status: TaskStatus | None = None


class ActivityRepository(Protocol):
    def add(self, activity: Activity) -> None: ...

    def get(self, activity_id: UUID) -> Activity | None: ...

    def save(self, activity: Activity, expected_version: int) -> None:
        """Optimistic concurrency: ConflictError if the stored version differs."""
        ...

    def list_visible(
        self, visibility: ActivityVisibility, filters: ActivityFilter, *, limit: int, offset: int
    ) -> list[Activity]: ...

    def count(self, visibility: ActivityVisibility, filters: ActivityFilter) -> int: ...


class SubjectChecker(Protocol):
    """Verifies the customer or deal an activity is attached to exists in this tenant."""

    def exists(self, subject_type: SubjectType, subject_id: UUID) -> bool: ...
