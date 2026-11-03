from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol
from uuid import UUID

from modules.access_control.domain.rbac import MemberRef, Principal
from modules.activities.domain.activity import (
    Activity,
    ActivityKind,
    SubjectRef,
    SubjectType,
    TaskPriority,
    TaskStatus,
    TaskType,
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
    assignee_user_id: UUID | None = None
    priority: TaskPriority | None = None
    due_from: date | None = None
    due_to: date | None = None
    # Open tasks whose due date is before this day (the company's "today").
    overdue_before: date | None = None
    # Tasks without a due date (the "no deadline" group).
    no_due_date: bool = False
    # Case-insensitive text in the title or body.
    search: str | None = None
    task_type: TaskType | None = None


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

    def count_open_tasks_for_subject(self, subject: SubjectRef) -> int:
        """Open tasks attached to one subject (used to cap open tasks per deal)."""
        ...


class SubjectChecker(Protocol):
    """Verifies the customer or deal an activity is attached to exists in this tenant."""

    def visible_to(self, principal: Principal, subject_type: SubjectType, subject_id: UUID) -> bool:
        """The record exists in this tenant AND the caller may see it (else it is "not found")."""
        ...


# An active member of the company and their department (shared assignment model).
MemberInfo = MemberRef


class MemberDirectory(Protocol):
    """Who belongs to the active company (tasks may only be assigned to its members)."""

    def active_member(self, user_id: UUID) -> MemberInfo | None: ...
