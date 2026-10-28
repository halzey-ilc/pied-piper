from __future__ import annotations

from django.db import models
from django.db.models import Q

from modules.communications.domain.conversation import (
    Channel,
    ConversationStatus,
    MessageDirection,
    MessageStatus,
)


class ConversationRecord(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    channel = models.CharField(max_length=10, choices=[(c.value, c.name.title()) for c in Channel])
    external_contact_id = models.CharField(max_length=255)
    contact_name = models.CharField(max_length=200, null=True, blank=True)  # noqa: DJ001
    status = models.CharField(
        max_length=8,
        choices=[(s.value, s.name.title()) for s in ConversationStatus],
        default=ConversationStatus.OPEN.value,
    )
    customer_id = models.UUIDField(null=True, blank=True)
    owner_user_id = models.UUIDField(null=True, blank=True)
    team_id = models.UUIDField(null=True, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)
    unread_count = models.PositiveIntegerField(default=0)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "communications_conversation"
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "external_contact_id"], name="conversation_channel_contact"
            ),
        ]
        indexes = [
            models.Index(fields=["status", "last_message_at"], name="conv_status_idx"),
            models.Index(fields=["owner_user_id"], name="conv_owner_idx"),
            models.Index(fields=["team_id"], name="conv_team_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.channel}:{self.external_contact_id}"


class MessageRecord(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    conversation = models.ForeignKey(
        ConversationRecord, on_delete=models.CASCADE, related_name="messages"
    )
    channel = models.CharField(max_length=10)
    direction = models.CharField(
        max_length=8, choices=[(d.value, d.name.title()) for d in MessageDirection]
    )
    text = models.TextField()
    status = models.CharField(
        max_length=8, choices=[(s.value, s.name.title()) for s in MessageStatus]
    )
    external_id = models.CharField(max_length=255, null=True, blank=True)  # noqa: DJ001
    sender_user_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "communications_message"
        constraints = [
            # Deduplicate inbound provider messages per channel.
            models.UniqueConstraint(
                fields=["channel", "external_id"],
                condition=Q(external_id__isnull=False),
                name="message_channel_external_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["conversation", "created_at"], name="message_conv_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.direction}:{self.id}"
