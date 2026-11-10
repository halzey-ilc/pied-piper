from __future__ import annotations

from django.db import models

from modules.telephony.domain.call import CallDirection, CallStatus, SubjectType


class CallRecord(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    direction = models.CharField(
        max_length=8, choices=[(d.value, d.name.title()) for d in CallDirection]
    )
    from_number = models.CharField(max_length=16)
    to_number = models.CharField(max_length=16)
    status = models.CharField(
        max_length=12, choices=[(s.value, s.name.title()) for s in CallStatus]
    )
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)
    recording_url = models.CharField(max_length=500, null=True, blank=True)  # noqa: DJ001
    external_id = models.CharField(max_length=255, null=True, blank=True)  # noqa: DJ001
    subject_type = models.CharField(  # noqa: DJ001
        max_length=8,
        null=True,
        blank=True,
        choices=[(s.value, s.name.title()) for s in SubjectType],
    )
    subject_id = models.UUIDField(null=True, blank=True)
    owner_user_id = models.UUIDField()
    team_id = models.UUIDField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)  # noqa: DJ001
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "telephony_call"
        indexes = [
            models.Index(fields=["status"], name="call_status_idx"),
            models.Index(fields=["direction"], name="call_direction_idx"),
            models.Index(fields=["subject_type", "subject_id"], name="call_subject_idx"),
            models.Index(fields=["owner_user_id"], name="call_owner_idx"),
            models.Index(fields=["team_id"], name="call_team_idx"),
            models.Index(fields=["created_at"], name="call_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.direction} {self.from_number}->{self.to_number} ({self.status})"


class TelephonyConnectionRecord(models.Model):
    """The company's PBX account (one per company). The integration key is encrypted;
    the event address token is stored only as a SHA-256 hash."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    provider = models.CharField(max_length=16, default="sipuni")
    account = models.CharField(max_length=16)
    encrypted_secret = models.BinaryField()
    company_number = models.CharField(max_length=16)
    fallback_owner_id = models.UUIDField()
    webhook_token_hash = models.CharField(max_length=64)
    extensions = models.JSONField(default=dict)  # short number -> user id
    country_code = models.CharField(max_length=4, default="996")
    call_language = models.CharField(max_length=2, default="ru")
    record_channels = models.CharField(max_length=16, default="mono")
    analyze_calls = models.BooleanField(default=True)
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "telephony_connection"

    def __str__(self) -> str:
        return f"{self.provider} {self.account}"
