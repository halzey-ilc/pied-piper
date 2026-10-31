from __future__ import annotations

from django.db import models

from modules.activities.domain.activity import (
    ActivityKind,
    CallDirection,
    SubjectType,
    TaskPriority,
    TaskStatus,
    TaskType,
)


class ActivityRecord(models.Model):
    """ORM storage for the Activity aggregate. Lives in each tenant's own database.

    One table holds all kinds; kind-specific columns are nullable and validated
    by the domain, not the database.
    """

    id = models.UUIDField(primary_key=True, editable=False)
    kind = models.CharField(max_length=8, choices=[(k.value, k.name.title()) for k in ActivityKind])
    subject_type = models.CharField(
        max_length=8, choices=[(s.value, s.name.title()) for s in SubjectType]
    )
    subject_id = models.UUIDField()
    owner_user_id = models.UUIDField()
    team_id = models.UUIDField(null=True, blank=True)
    title = models.CharField(max_length=200, null=True, blank=True)  # noqa: DJ001
    body = models.TextField(null=True, blank=True)  # noqa: DJ001
    # task
    due_date = models.DateField(null=True, blank=True)
    task_status = models.CharField(  # noqa: DJ001
        max_length=10,
        null=True,
        blank=True,
        choices=[(s.value, s.name.title()) for s in TaskStatus],
    )
    task_type = models.CharField(  # noqa: DJ001
        max_length=10,
        null=True,
        blank=True,
        choices=[(t.value, t.name.title()) for t in TaskType],
    )
    priority = models.CharField(  # noqa: DJ001
        max_length=8,
        null=True,
        blank=True,
        choices=[(p.value, p.name.title()) for p in TaskPriority],
    )
    assignee_user_id = models.UUIDField(null=True, blank=True)
    created_by = models.UUIDField(null=True, blank=True)
    result = models.CharField(max_length=5000, null=True, blank=True)  # noqa: DJ001
    completed_at = models.DateTimeField(null=True, blank=True)
    # call
    direction = models.CharField(  # noqa: DJ001
        max_length=8,
        null=True,
        blank=True,
        choices=[(d.value, d.name.title()) for d in CallDirection],
    )
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    occurred_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=200, null=True, blank=True)  # noqa: DJ001
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "activities_activity"
        indexes = [
            models.Index(
                fields=["subject_type", "subject_id", "created_at"], name="act_subject_idx"
            ),
            models.Index(fields=["kind"], name="act_kind_idx"),
            models.Index(fields=["owner_user_id"], name="act_owner_idx"),
            models.Index(fields=["assignee_user_id"], name="act_assignee_idx"),
            models.Index(fields=["team_id"], name="act_team_idx"),
            models.Index(fields=["task_status"], name="act_task_status_idx"),
            models.Index(fields=["created_at"], name="act_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} on {self.subject_type}:{self.subject_id}"
