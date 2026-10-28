"""Per-user notification preferences: which categories are delivered in-app."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from modules.notifications.domain.notification import NotificationCategory


@dataclass(slots=True)
class NotificationPreferences:
    user_id: UUID
    # Categories the user has turned OFF. Everything not listed is on by default.
    muted: frozenset[NotificationCategory] = field(default_factory=frozenset)
    version: int = 1

    def is_enabled(self, category: NotificationCategory) -> bool:
        return category not in self.muted

    def set_category(self, category: NotificationCategory, *, enabled: bool) -> bool:
        currently = self.is_enabled(category)
        if currently == enabled:
            return False
        self.muted = self.muted - {category} if enabled else self.muted | {category}
        self.version += 1
        return True
