"""Input validation and output shaping only; business rules live in the domain."""

from __future__ import annotations

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
    version = serializers.IntegerField(required=False, min_value=1)


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


def activity_to_dict(activity: Activity) -> dict[str, Any]:
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
        "completed_at": _iso(activity.completed_at),
        "direction": activity.direction.value if activity.direction else None,
        "duration_seconds": activity.duration_seconds,
        "occurred_at": _iso(activity.occurred_at),
        "outcome": activity.outcome,
        "version": activity.version,
        "created_at": activity.created_at.isoformat(),
    }
