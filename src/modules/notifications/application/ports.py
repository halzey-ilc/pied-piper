from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

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
