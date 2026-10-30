"""Input validation and output shaping only; business rules live in the domain."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from rest_framework import serializers

from modules.activities.domain.activity import (
    MAX_BODY_LENGTH,
    MAX_DURATION_SECONDS,
    MAX_OUTCOME_LENGTH,
    MAX_TITLE_LENGTH,
    Activity,
    ActivityKind,
    CallDirection,
    SubjectType,
    TaskPriority,
    TaskStatus,
    TaskType,
)


class _SubjectSerializer(serializers.Serializer[dict[str, Any]]):
    subject_type = serializers.ChoiceField(choices=[s.value for s in SubjectType])
    subject_id = serializers.UUIDField()


class ActivityCreateSerializer(_SubjectSerializer):
    """Create any activity kind. Required fields depend on `kind` (enforced by the domain)."""

    kind = serializers.ChoiceField(choices=[k.value for k in ActivityKind])
    title = serializers.CharField(required=False, allow_blank=True, max_length=MAX_TITLE_LENGTH)
    body = serializers.CharField(required=False, allow_blank=True, max_length=MAX_BODY_LENGTH)
    # task
    due_date = serializers.DateField(required=False, allow_null=True)
    task_type = serializers.ChoiceField(
        required=False, choices=[t.value for t in TaskType], default=TaskType.OTHER.value
    )
    priority = serializers.ChoiceField(
        required=False, choices=[p.value for p in TaskPriority], default=TaskPriority.NORMAL.value
    )
    assignee_user_id = serializers.UUIDField(required=False, allow_null=True)
    # call
    direction = serializers.ChoiceField(required=False, choices=[d.value for d in CallDirection])
    occurred_at = serializers.DateTimeField(required=False)
    duration_seconds = serializers.IntegerField(
        required=False, min_value=0, max_value=MAX_DURATION_SECONDS, default=0
    )
    outcome = serializers.CharField(required=False, allow_blank=True, max_length=MAX_OUTCOME_LENGTH)


class ActivityUpdateSerializer(serializers.Serializer[dict[str, Any]]):
    title = serializers.CharField(required=False, allow_blank=True, max_length=MAX_TITLE_LENGTH)
    body = serializers.CharField(required=False, allow_blank=True, max_length=MAX_BODY_LENGTH)
    due_date = serializers.DateField(required=False, allow_null=True)
    priority = serializers.ChoiceField(required=False, choices=[p.value for p in TaskPriority])
    version = serializers.IntegerField(required=False, min_value=1)


class TaskResultSerializer(serializers.Serializer[dict[str, Any]]):
    result = serializers.CharField(required=False, allow_blank=True, max_length=MAX_BODY_LENGTH)


class TaskReassignSerializer(serializers.Serializer[dict[str, Any]]):
    assignee_user_id = serializers.UUIDField()


class ActivityReadSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    kind = serializers.CharField(read_only=True)
    subject_type = serializers.CharField(read_only=True)
    subject_id = serializers.UUIDField(read_only=True)
    owner_user_id = serializers.UUIDField(read_only=True)
    title = serializers.CharField(read_only=True, allow_null=True)
    body = serializers.CharField(read_only=True, allow_null=True)
    due_date = serializers.DateField(read_only=True, allow_null=True)
    task_status = serializers.CharField(read_only=True, allow_null=True)
    task_type = serializers.CharField(read_only=True, allow_null=True)
    priority = serializers.CharField(read_only=True, allow_null=True)
    assignee_user_id = serializers.UUIDField(read_only=True, allow_null=True)
    created_by = serializers.UUIDField(read_only=True, allow_null=True)
    result = serializers.CharField(read_only=True, allow_null=True)
    is_overdue = serializers.BooleanField(read_only=True)
    due_group = serializers.CharField(read_only=True, allow_null=True)
    completed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    direction = serializers.CharField(read_only=True, allow_null=True)
    duration_seconds = serializers.IntegerField(read_only=True, allow_null=True)
    occurred_at = serializers.DateTimeField(read_only=True, allow_null=True)
    outcome = serializers.CharField(read_only=True, allow_null=True)
    version = serializers.IntegerField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class ActivityListSerializer(serializers.Serializer[dict[str, Any]]):
    count = serializers.IntegerField(read_only=True)
    results = ActivityReadSerializer(many=True, read_only=True)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _due_group(activity: Activity, today: date | None) -> str | None:
    """Bucket an open task's due date (in the company's day) for the UI:
    overdue / today / this_week / later; None when there is no open due date."""
    if (
        activity.kind is not ActivityKind.TASK
        or activity.task_status is not TaskStatus.OPEN
        or activity.due_date is None
        or today is None
    ):
        return None
    if activity.due_date < today:
        return "overdue"
    if activity.due_date == today:
        return "today"
    if activity.due_date <= today + timedelta(days=6):
        return "this_week"
    return "later"


def activity_to_dict(activity: Activity, today: date | None = None) -> dict[str, Any]:
    is_overdue = (
        activity.kind is ActivityKind.TASK
        and activity.task_status is TaskStatus.OPEN
        and activity.due_date is not None
        and today is not None
        and activity.due_date < today
    )
    return {
        "id": str(activity.id),
        "kind": activity.kind.value,
        "subject_type": activity.subject.type.value,
        "subject_id": str(activity.subject.id),
        "owner_user_id": str(activity.owner_user_id),
        "title": activity.title,
        "body": activity.body,
        "due_date": _iso(activity.due_date),
        "task_status": activity.task_status.value if activity.task_status else None,
        "task_type": activity.task_type.value if activity.task_type else None,
        "priority": activity.priority.value if activity.priority else None,
        "assignee_user_id": str(activity.assignee_user_id) if activity.assignee_user_id else None,
        "created_by": str(activity.created_by) if activity.created_by else None,
        "result": activity.result,
        "is_overdue": is_overdue,
        "due_group": _due_group(activity, today),
        "completed_at": _iso(activity.completed_at),
        "direction": activity.direction.value if activity.direction else None,
        "duration_seconds": activity.duration_seconds,
        "occurred_at": _iso(activity.occurred_at),
        "outcome": activity.outcome,
        "version": activity.version,
        "created_at": activity.created_at.isoformat(),
    }
