"""Per-user notification preferences: which categories are delivered in-app."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from modules.notifications.domain.notification import NotificationCategory
from shared.domain.errors import DomainValidationError

# Task due dates are calendar dates (no time), so the reminder lead time is in days.
DEFAULT_REMINDER_DAYS_BEFORE = 1
MAX_REMINDER_DAYS_BEFORE = 7


@dataclass(slots=True)
class NotificationPreferences:
    user_id: UUID
    # Categories the user has turned OFF. Everything not listed is on by default.
    muted: frozenset[NotificationCategory] = field(default_factory=frozenset)
    version: int = 1
    # How many days before the due date to remind about an open task (0 = on the day).
    reminder_days_before: int = DEFAULT_REMINDER_DAYS_BEFORE

    def is_enabled(self, category: NotificationCategory) -> bool:
        return category not in self.muted

    def set_category(self, category: NotificationCategory, *, enabled: bool) -> bool:
        currently = self.is_enabled(category)
        if currently == enabled:
            return False
        self.muted = self.muted - {category} if enabled else self.muted | {category}
        self.version += 1
        return True

    def set_reminder_days(self, days: int) -> bool:
        if not 0 <= days <= MAX_REMINDER_DAYS_BEFORE:
            raise DomainValidationError(
                f"reminder_days_before must be between 0 and {MAX_REMINDER_DAYS_BEFORE}."
            )
        if days == self.reminder_days_before:
            return False
        self.reminder_days_before = days
        self.version += 1
        return True
