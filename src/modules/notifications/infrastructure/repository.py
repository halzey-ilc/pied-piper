from __future__ import annotations

from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone

from modules.notifications.application.ports import NotificationFilter
from modules.notifications.domain.notification import Notification, NotificationCategory
from modules.notifications.domain.preferences import NotificationPreferences
from modules.notifications.infrastructure.models import (
    NotificationPreferenceRecord,
    NotificationRecord,
)


def _to_domain(row: NotificationRecord) -> Notification:
    return Notification(
        id=row.id,
        recipient_user_id=row.recipient_user_id,
        category=NotificationCategory(row.category),
        title=row.title,
        body=row.body,
        source_event_id=row.source_event_id,
        link=row.link,
        read=row.read,
        read_at=row.read_at,
        created_at=row.created_at,
    )


class DjangoNotificationRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def _qs(self) -> QuerySet[NotificationRecord]:
        return NotificationRecord.objects.using(self.using)

    def _filtered(
        self, recipient_user_id: UUID, filters: NotificationFilter
    ) -> QuerySet[NotificationRecord]:
        qs = self._qs().filter(recipient_user_id=recipient_user_id)
        if filters.unread_only:
            qs = qs.filter(read=False)
        if filters.category is not None:
            qs = qs.filter(category=filters.category.value)
        return qs

    def add(self, notification: Notification) -> bool:
        fields = {
            "id": notification.id,
            "recipient_user_id": notification.recipient_user_id,
            "category": notification.category.value,
            "title": notification.title,
            "body": notification.body,
            "source_event_id": notification.source_event_id,
            "link": notification.link,
            "read": notification.read,
            "created_at": notification.created_at,
        }
        if notification.source_event_id is None:
            self._qs().create(**fields)
            return True
        try:
            with transaction.atomic(using=self.using):
                self._qs().create(**fields)
        except IntegrityError:
            return False
        return True

    def get_for(self, recipient_user_id: UUID, notification_id: UUID) -> Notification | None:
        row = self._qs().filter(recipient_user_id=recipient_user_id, id=notification_id).first()
        return None if row is None else _to_domain(row)

    def save(self, notification: Notification) -> None:
        self._qs().filter(id=notification.id).update(
            read=notification.read, read_at=notification.read_at
        )

    def list_for(
        self, recipient_user_id: UUID, filters: NotificationFilter, *, limit: int, offset: int
    ) -> list[Notification]:
        rows = self._filtered(recipient_user_id, filters).order_by("-created_at", "id")[
            offset : offset + limit
        ]
        return [_to_domain(row) for row in rows]

    def count(self, recipient_user_id: UUID, filters: NotificationFilter) -> int:
        return self._filtered(recipient_user_id, filters).count()

    def unread_count(self, recipient_user_id: UUID) -> int:
        return self._qs().filter(recipient_user_id=recipient_user_id, read=False).count()

    def mark_all_read(self, recipient_user_id: UUID) -> int:
        return (
            self._qs()
            .filter(recipient_user_id=recipient_user_id, read=False)
            .update(read=True, read_at=timezone.now())
        )


class DjangoPreferenceRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def get(self, user_id: UUID) -> NotificationPreferences:
        row = NotificationPreferenceRecord.objects.using(self.using).filter(user_id=user_id).first()
        if row is None:
            return NotificationPreferences(user_id=user_id)
        muted = frozenset(NotificationCategory(c) for c in row.muted)
        return NotificationPreferences(user_id=user_id, muted=muted, version=row.version)

    def save(self, preferences: NotificationPreferences) -> None:
        NotificationPreferenceRecord.objects.using(self.using).update_or_create(
            user_id=preferences.user_id,
            defaults={
                "muted": [c.value for c in sorted(preferences.muted)],
                "version": preferences.version,
            },
        )
