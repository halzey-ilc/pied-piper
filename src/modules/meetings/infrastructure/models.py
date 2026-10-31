from __future__ import annotations

from django.db import models

from modules.meetings.domain.meeting import (
    ConfirmationSource,
    MeetingFormat,
    MeetingStatus,
    SubjectType,
)


class MeetingRecord(models.Model):
    """ORM storage for the Meeting aggregate. Participants and reminders are part
    of the aggregate, stored as JSON on the same row."""

    id = models.UUIDField(primary_key=True, editable=False)
    title = models.CharField(max_length=200)
    description = models.TextField(null=True, blank=True)  # noqa: DJ001
    location = models.CharField(max_length=200, null=True, blank=True)  # noqa: DJ001
    subject_type = models.CharField(  # noqa: DJ001
        max_length=8,
        null=True,
        blank=True,
        choices=[(s.value, s.name.title()) for s in SubjectType],
    )
    subject_id = models.UUIDField(null=True, blank=True)
    status = models.CharField(
        max_length=12,
        choices=[(s.value, s.name.title()) for s in MeetingStatus],
        default=MeetingStatus.SCHEDULED.value,
    )
    scheduled_start = models.DateTimeField()
    scheduled_end = models.DateTimeField()
    owner_user_id = models.UUIDField()
    team_id = models.UUIDField(null=True, blank=True)
    format = models.CharField(
        max_length=8,
        choices=[(f.value, f.name.title()) for f in MeetingFormat],
        default=MeetingFormat.OFFICE.value,
    )
    host_user_id = models.UUIDField(null=True, blank=True)
    booked_by_user_id = models.UUIDField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by_user_id = models.UUIDField(null=True, blank=True)
    confirmation_source = models.CharField(  # noqa: DJ001
        max_length=8,
        null=True,
        blank=True,
        choices=[(s.value, s.name.title()) for s in ConfirmationSource],
    )
    participants = models.JSONField(default=list)
    reminders = models.JSONField(default=list)
    outcome = models.TextField(null=True, blank=True)  # noqa: DJ001
    cancel_reason = models.CharField(max_length=500, null=True, blank=True)  # noqa: DJ001
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "meetings_meeting"
        indexes = [
            models.Index(fields=["status"], name="meeting_status_idx"),
            models.Index(fields=["scheduled_start"], name="meeting_start_idx"),
            models.Index(fields=["subject_type", "subject_id"], name="meeting_subject_idx"),
            models.Index(fields=["owner_user_id"], name="meeting_owner_idx"),
            models.Index(fields=["team_id"], name="meeting_team_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.status})"
