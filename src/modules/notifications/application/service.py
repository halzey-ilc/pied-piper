"""Notification use cases. Users act on their own notifications only; the notify
entry point is called by event handlers (system) and respects preferences."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from modules.notifications.application.ports import (
    NotificationFilter,
    NotificationRepository,
    PreferenceRepository,
)
from modules.notifications.domain.notification import Notification, NotificationCategory
from modules.notifications.domain.preferences import NotificationPreferences
from shared.application.ports import UnitOfWork
from shared.domain.errors import NotFoundError


@dataclass(frozen=True, slots=True)
class NotificationPage:
    items: list[Notification]
    total: int
    unread: int


class NotificationService:
    def __init__(
        self,
        notifications: NotificationRepository,
        preferences: PreferenceRepository,
        uow: UnitOfWork,
    ) -> None:
        self._repo = notifications
        self._prefs = preferences
        self._uow = uow

    # --- system entry point (event handlers) --------------------------------

    def notify(
        self,
        *,
        recipient_user_id: UUID,
        category: NotificationCategory,
        title: str,
        body: str,
        source_event_id: UUID | None = None,
        link: str | None = None,
    ) -> bool:
        """Create a notification if the recipient has the category enabled. Idempotent
        on (recipient, source_event_id). Returns True if one was created."""
        if not self._prefs.get(recipient_user_id).is_enabled(category):
            return False
        notification = Notification.create(
            recipient_user_id=recipient_user_id,
            category=category,
            title=title,
            body=body,
            source_event_id=source_event_id,
            link=link,
        )
        return self._repo.add(notification)

    # --- user-facing --------------------------------------------------------

    def list_for(
        self, user_id: UUID, filters: NotificationFilter, *, limit: int, offset: int
    ) -> NotificationPage:
        return NotificationPage(
            items=self._repo.list_for(user_id, filters, limit=limit, offset=offset),
            total=self._repo.count(user_id, filters),
            unread=self._repo.unread_count(user_id),
        )

    def unread_count(self, user_id: UUID) -> int:
        return self._repo.unread_count(user_id)

    def mark_read(self, user_id: UUID, notification_id: UUID) -> Notification:
        with self._uow:
            notification = self._repo.get_for(user_id, notification_id)
            if notification is None:
                raise NotFoundError("Notification not found.")
            if notification.mark_read():
                self._repo.save(notification)
        return notification

    def mark_all_read(self, user_id: UUID) -> int:
        with self._uow:
            return self._repo.mark_all_read(user_id)

    def get_preferences(self, user_id: UUID) -> NotificationPreferences:
        return self._prefs.get(user_id)

    def set_preference(
        self, user_id: UUID, category: NotificationCategory, *, enabled: bool
    ) -> NotificationPreferences:
        with self._uow:
            preferences = self._prefs.get(user_id)
            if preferences.set_category(category, enabled=enabled):
                self._prefs.save(preferences)
        return preferences
