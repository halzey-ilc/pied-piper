from __future__ import annotations

import uuid

from django.db import models
from django.db.models import Q

from modules.notifications.domain.notification import NotificationCategory


class NotificationRecord(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    recipient_user_id = models.UUIDField()
    category = models.CharField(
        max_length=12, choices=[(c.value, c.name.title()) for c in NotificationCategory]
    )
    title = models.CharField(max_length=200)
    body = models.CharField(max_length=1000, blank=True, default="")
    source_event_id = models.UUIDField(null=True, blank=True)
    link = models.CharField(max_length=255, null=True, blank=True)  # noqa: DJ001
    read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "notifications_notification"
        constraints = [
            # One notification per event per recipient (idempotent event handlers).
            models.UniqueConstraint(
                fields=["recipient_user_id", "source_event_id"],
                condition=Q(source_event_id__isnull=False),
                name="notification_event_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=["recipient_user_id", "read", "created_at"], name="notif_recipient_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.category}:{self.title}"


class NotificationPreferenceRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.UUIDField(unique=True)
    muted = models.JSONField(default=list)
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "notifications_preference"

    def __str__(self) -> str:
        return str(self.user_id)
