"""Input validation and output shaping only; business rules live in the domain."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from modules.meetings.domain.meeting import (
    MAX_REMINDER_MINUTES,
    MAX_TITLE_LENGTH,
    ConfirmationSource,
    Meeting,
    MeetingFormat,
    ParticipantResponse,
    SubjectType,
)

_OUTCOMES = ["held", "no_show", "rescheduled", "cancelled"]


class ParticipantInputSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(max_length=120)
    email = serializers.EmailField(required=False, allow_blank=True, max_length=254)
    user_id = serializers.UUIDField(required=False, allow_null=True)


class MeetingCreateSerializer(serializers.Serializer[dict[str, Any]]):
    title = serializers.CharField(max_length=MAX_TITLE_LENGTH)
    scheduled_start = serializers.DateTimeField()
    scheduled_end = serializers.DateTimeField()
    description = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    location = serializers.CharField(required=False, allow_blank=True, max_length=200)
    subject_type = serializers.ChoiceField(
        required=False, allow_null=True, choices=[s.value for s in SubjectType]
    )
    subject_id = serializers.UUIDField(required=False, allow_null=True)
    format = serializers.ChoiceField(
        required=False, choices=[f.value for f in MeetingFormat], default=MeetingFormat.OFFICE.value
    )
    host_user_id = serializers.UUIDField(required=False, allow_null=True)
    booked_by_user_id = serializers.UUIDField(required=False, allow_null=True)
    participants = ParticipantInputSerializer(many=True, required=False, default=list)
    reminders = serializers.ListField(
        child=serializers.IntegerField(min_value=0, max_value=MAX_REMINDER_MINUTES),
        required=False,
        default=list,
        max_length=10,
    )


class MeetingUpdateSerializer(serializers.Serializer[dict[str, Any]]):
    title = serializers.CharField(required=False, max_length=MAX_TITLE_LENGTH)
    description = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    location = serializers.CharField(required=False, allow_blank=True, max_length=200)
    reminders = serializers.ListField(
        child=serializers.IntegerField(min_value=0, max_value=MAX_REMINDER_MINUTES),
        required=False,
        max_length=10,
    )
    version = serializers.IntegerField(required=False, min_value=1)


class MeetingRescheduleSerializer(serializers.Serializer[dict[str, Any]]):
    scheduled_start = serializers.DateTimeField()
    scheduled_end = serializers.DateTimeField()


class MeetingCompleteSerializer(serializers.Serializer[dict[str, Any]]):
    outcome = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class MeetingCancelSerializer(serializers.Serializer[dict[str, Any]]):
    reason = serializers.CharField(max_length=500)


class MeetingConfirmSerializer(serializers.Serializer[dict[str, Any]]):
    source = serializers.ChoiceField(  # type: ignore[assignment]  # DRF Field.source
        required=False,
        choices=[s.value for s in ConfirmationSource],
        default=ConfirmationSource.MANUAL.value,
    )


class MeetingOutcomeSerializer(serializers.Serializer[dict[str, Any]]):
    outcome = serializers.ChoiceField(choices=_OUTCOMES)
    new_start = serializers.DateTimeField(required=False, allow_null=True)
    new_end = serializers.DateTimeField(required=False, allow_null=True)
    comment = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class ParticipantResponseSerializer(serializers.Serializer[dict[str, Any]]):
    key = serializers.CharField(max_length=254)
    response = serializers.ChoiceField(choices=[r.value for r in ParticipantResponse])


class ParticipantReadSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(read_only=True)
    email = serializers.CharField(read_only=True, allow_null=True)
    user_id = serializers.UUIDField(read_only=True, allow_null=True)
    response = serializers.CharField(read_only=True)


class MeetingReadSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    title = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True, allow_null=True)
    location = serializers.CharField(read_only=True, allow_null=True)
    subject_type = serializers.CharField(read_only=True, allow_null=True)
    subject_id = serializers.UUIDField(read_only=True, allow_null=True)
    scheduled_start = serializers.DateTimeField(read_only=True)
    scheduled_end = serializers.DateTimeField(read_only=True)
    duration_minutes = serializers.IntegerField(read_only=True)
    owner_user_id = serializers.UUIDField(read_only=True)
    format = serializers.CharField(read_only=True)
    host_user_id = serializers.UUIDField(read_only=True, allow_null=True)
    booked_by_user_id = serializers.UUIDField(read_only=True, allow_null=True)
    confirmed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    confirmed_by_user_id = serializers.UUIDField(read_only=True, allow_null=True)
    confirmation_source = serializers.CharField(read_only=True, allow_null=True)
    participants = ParticipantReadSerializer(many=True, read_only=True)
    reminders = serializers.ListField(child=serializers.IntegerField(), read_only=True)
    outcome = serializers.CharField(read_only=True, allow_null=True)
    cancel_reason = serializers.CharField(read_only=True, allow_null=True)
    version = serializers.IntegerField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class MeetingListSerializer(serializers.Serializer[dict[str, Any]]):
    count = serializers.IntegerField(read_only=True)
    results = MeetingReadSerializer(many=True, read_only=True)


def meeting_to_dict(meeting: Meeting) -> dict[str, Any]:
    return {
        "id": str(meeting.id),
        "title": meeting.title,
        "status": meeting.status.value,
        "description": meeting.description,
        "location": meeting.location,
        "subject_type": meeting.link.type.value if meeting.link else None,
        "subject_id": str(meeting.link.id) if meeting.link else None,
        "scheduled_start": meeting.scheduled_start.isoformat(),
        "scheduled_end": meeting.scheduled_end.isoformat(),
        "duration_minutes": meeting.duration_minutes,
        "owner_user_id": str(meeting.owner_user_id),
        "format": meeting.format.value,
        "host_user_id": str(meeting.host_user_id) if meeting.host_user_id else None,
        "booked_by_user_id": (
            str(meeting.booked_by_user_id) if meeting.booked_by_user_id else None
        ),
        "confirmed_at": meeting.confirmed_at.isoformat() if meeting.confirmed_at else None,
        "confirmed_by_user_id": (
            str(meeting.confirmed_by_user_id) if meeting.confirmed_by_user_id else None
        ),
        "confirmation_source": (
            meeting.confirmation_source.value if meeting.confirmation_source else None
        ),
        "participants": [
            {
                "name": p.name,
                "email": p.email,
                "user_id": str(p.user_id) if p.user_id else None,
                "response": p.response.value,
            }
            for p in meeting.participants
        ],
        "reminders": list(meeting.reminders),
        "outcome": meeting.outcome,
        "cancel_reason": meeting.cancel_reason,
        "version": meeting.version,
        "created_at": meeting.created_at.isoformat(),
    }
