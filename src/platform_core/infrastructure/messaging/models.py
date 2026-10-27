"""Outbox and inbox tables. They live in every database (control plane and each tenant)
so that events are written in the same transaction as the business change."""

from __future__ import annotations

import uuid

from django.db import models
from django.db.models import Q
from django.utils import timezone


class OutboxStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PUBLISHED = "published", "Published"
    FAILED = "failed", "Failed"


class OutboxMessage(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)  # == domain event id
    event_type = models.CharField(max_length=150)
    aggregate_type = models.CharField(max_length=100)
    aggregate_id = models.CharField(max_length=64)
    payload = models.JSONField()
    correlation_id = models.CharField(max_length=64, blank=True, default="")
    occurred_at = models.DateTimeField()
    status = models.CharField(
        max_length=16, choices=OutboxStatus.choices, default=OutboxStatus.PENDING
    )
    attempts = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    published_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "messaging_outbox"
        indexes = [
            models.Index(
                fields=["available_at"],
                name="outbox_pending_due_idx",
                condition=Q(status="pending"),
            ),
            models.Index(fields=["aggregate_type", "aggregate_id"], name="outbox_aggregate_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(status="published") | Q(published_at__isnull=False),
                name="outbox_published_has_timestamp",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.event_type}:{self.id}"


class InboxStatus(models.TextChoices):
    RECEIVED = "received", "Received"
    PROCESSED = "processed", "Processed"
    FAILED = "failed", "Failed"


class InboxMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.CharField(max_length=64)  # e.g. "webhook.whatsapp", "event"
    message_id = models.CharField(max_length=255)  # external / event id
    consumer = models.CharField(max_length=100, default="")
    payload = models.JSONField()
    correlation_id = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(
        max_length=16, choices=InboxStatus.choices, default=InboxStatus.RECEIVED
    )
    attempts = models.PositiveIntegerField(default=0)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        db_table = "messaging_inbox"
        constraints = [
            models.UniqueConstraint(
                fields=["source", "message_id", "consumer"], name="inbox_message_unique"
            ),
        ]
        indexes = [
            models.Index(fields=["status", "received_at"], name="inbox_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.source}:{self.message_id}"
