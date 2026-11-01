from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from modules.organizations.infrastructure.models import Organization
from modules.platform_admin.domain.support import ErrorStatus, TicketKind, TicketStatus


class ErrorEvent(models.Model):
    """An unexpected server error (HTTP 500), recorded for the platform administrator.

    Holds no exception message, request body, headers or query string — they may carry
    personal data or secrets. The request id links the record to the server logs.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    occurred_at = models.DateTimeField(auto_now_add=True)
    component = models.CharField(max_length=100)
    view = models.CharField(max_length=100)
    method = models.CharField(max_length=10)
    route = models.CharField(max_length=200)
    exception_type = models.CharField(max_length=100)
    request_id = models.CharField(max_length=64, blank=True, default="")
    tenant_slug = models.CharField(max_length=40, blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=[(s.value, s.name.title()) for s in ErrorStatus],
        default=ErrorStatus.NEW.value,
    )
    handled_by = models.UUIDField(null=True, blank=True)
    handled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "platform_admin_error_event"
        indexes = [
            models.Index(fields=["occurred_at"], name="error_event_occurred_idx"),
            models.Index(fields=["status"], name="error_event_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.exception_type}@{self.route}"


class SupportTicket(models.Model):
    """A question, problem report or feedback sent by a company member to the platform."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="support_tickets"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="support_tickets"
    )
    kind = models.CharField(max_length=16, choices=[(k.value, k.name.title()) for k in TicketKind])
    subject = models.CharField(max_length=200)
    body = models.TextField(max_length=5000)
    status = models.CharField(
        max_length=16,
        choices=[(s.value, s.name.title()) for s in TicketStatus],
        default=TicketStatus.NEW.value,
    )
    # Seen only by the platform administrator.
    internal_note = models.TextField(max_length=5000, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "platform_admin_support_ticket"
        indexes = [
            models.Index(fields=["status", "created_at"], name="ticket_status_created_idx"),
            models.Index(fields=["organization", "created_at"], name="ticket_org_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.organization_id}:{self.subject}"
