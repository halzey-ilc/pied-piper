from __future__ import annotations

from typing import Any

from rest_framework import serializers

from modules.telephony.domain.call import (
    MAX_DURATION_SECONDS,
    Call,
    CallDirection,
    SubjectType,
)

_TERMINAL = ["completed", "missed", "failed"]


class CallLogSerializer(serializers.Serializer[dict[str, Any]]):
    direction = serializers.ChoiceField(choices=[d.value for d in CallDirection])
    from_number = serializers.CharField(max_length=32)
    to_number = serializers.CharField(max_length=32)
    status = serializers.ChoiceField(choices=_TERMINAL)
    started_at = serializers.DateTimeField()
    duration_seconds = serializers.IntegerField(
        min_value=0, max_value=MAX_DURATION_SECONDS, default=0
    )
    recording_url = serializers.URLField(required=False, allow_blank=True, max_length=500)
    external_id = serializers.CharField(required=False, allow_blank=True, max_length=255)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    subject_type = serializers.ChoiceField(
        required=False, allow_null=True, choices=[s.value for s in SubjectType]
    )
    subject_id = serializers.UUIDField(required=False, allow_null=True)


class CallRequestSerializer(serializers.Serializer[dict[str, Any]]):
    from_number = serializers.CharField(max_length=32)
    to_number = serializers.CharField(max_length=32)
    subject_type = serializers.ChoiceField(
        required=False, allow_null=True, choices=[s.value for s in SubjectType]
    )
    subject_id = serializers.UUIDField(required=False, allow_null=True)


class CallAdvanceSerializer(serializers.Serializer[dict[str, Any]]):
    status = serializers.ChoiceField(choices=["ringing", "in_progress"])


class CallEndSerializer(serializers.Serializer[dict[str, Any]]):
    status = serializers.ChoiceField(choices=_TERMINAL)
    duration_seconds = serializers.IntegerField(
        min_value=0, max_value=MAX_DURATION_SECONDS, default=0
    )
    recording_url = serializers.URLField(required=False, allow_blank=True, max_length=500)


class CallReadSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    direction = serializers.CharField(read_only=True)
    from_number = serializers.CharField(read_only=True)
    to_number = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    started_at = serializers.DateTimeField(read_only=True, allow_null=True)
    ended_at = serializers.DateTimeField(read_only=True, allow_null=True)
    duration_seconds = serializers.IntegerField(read_only=True)
    recording_url = serializers.CharField(read_only=True, allow_null=True)
    external_id = serializers.CharField(read_only=True, allow_null=True)
    subject_type = serializers.CharField(read_only=True, allow_null=True)
    subject_id = serializers.UUIDField(read_only=True, allow_null=True)
    owner_user_id = serializers.UUIDField(read_only=True)
    notes = serializers.CharField(read_only=True, allow_null=True)
    version = serializers.IntegerField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class CallListSerializer(serializers.Serializer[dict[str, Any]]):
    count = serializers.IntegerField(read_only=True)
    results = CallReadSerializer(many=True, read_only=True)


def call_to_dict(call: Call) -> dict[str, Any]:
    return {
        "id": str(call.id),
        "direction": call.direction.value,
        "from_number": str(call.from_number),
        "to_number": str(call.to_number),
        "status": call.status.value,
        "started_at": call.started_at.isoformat() if call.started_at else None,
        "ended_at": call.ended_at.isoformat() if call.ended_at else None,
        "duration_seconds": call.duration_seconds,
        "recording_url": call.recording_url,
        "external_id": call.external_id,
        "subject_type": call.link.type.value if call.link else None,
        "subject_id": str(call.link.id) if call.link else None,
        "owner_user_id": str(call.owner_user_id),
        "notes": call.notes,
        "version": call.version,
        "created_at": call.created_at.isoformat(),
    }
