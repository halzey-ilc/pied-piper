from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from modules.notifications.domain.daily_reports import (
    EveningFigures,
    MorningFigures,
    ReportKind,
    StaffMember,
)
from modules.notifications.domain.deadlines import TaskDeadline
from modules.notifications.domain.notification import Notification, NotificationCategory
from modules.notifications.domain.preferences import NotificationPreferences


@dataclass(frozen=True, slots=True)
class NotificationFilter:
    unread_only: bool = False
    category: NotificationCategory | None = None


class NotificationRepository(Protocol):
    def add(self, notification: Notification) -> bool:
        """Insert; return False if an identical (recipient, source_event_id) already exists."""
        ...

    def get_for(self, recipient_user_id: UUID, notification_id: UUID) -> Notification | None: ...

    def save(self, notification: Notification) -> None: ...

    def list_for(
        self, recipient_user_id: UUID, filters: NotificationFilter, *, limit: int, offset: int
    ) -> list[Notification]: ...

    def count(self, recipient_user_id: UUID, filters: NotificationFilter) -> int: ...

    def unread_count(self, recipient_user_id: UUID) -> int: ...

    def mark_all_read(self, recipient_user_id: UUID) -> int: ...


class PreferenceRepository(Protocol):
    def get(self, user_id: UUID) -> NotificationPreferences: ...

    def save(self, preferences: NotificationPreferences) -> None: ...


class TaskDeadlineSource(Protocol):
    def open_tasks_due_until(self, until: date) -> list[TaskDeadline]:
        """Open, assigned tasks with a due date on or before ``until`` (incl. overdue)."""
        ...

    def subject_titles(self, tasks: list[TaskDeadline]) -> dict[UUID, str]:
        """Display names of the tasks' deals/customers, by subject id (for the text)."""
        ...


class DepartmentHeads(Protocol):
    def head_of(self, user_id: UUID) -> UUID | None:
        """User id of the active head of sales of the user's department, if any."""
        ...


class ReportRoster(Protocol):
    def staff(self) -> list[StaffMember]:
        """Active members of the (active) company with active accounts, with product roles,
        display names and departments. Dismissed/inactive members are not listed."""
        ...


class DailyFiguresSource(Protocol):
    """Read-only day figures per user from the company's database (company-local day)."""

    def absent(self, user_ids: list[UUID], day: date) -> set[UUID]: ...

    def morning(
        self, user_ids: list[UUID], *, day: date, now: datetime
    ) -> dict[UUID, MorningFigures]: ...

    def evening(
        self, user_ids: list[UUID], *, day: date, now: datetime
    ) -> dict[UUID, EveningFigures]: ...


class ReportRuns(Protocol):
    """Per-company markers of reports already sent for a company-local day."""

    def done(self, day: date) -> set[ReportKind]: ...

    def mark_done(self, kind: ReportKind, day: date) -> None: ...
