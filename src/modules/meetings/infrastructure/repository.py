from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db.models import F, Q, QuerySet

from modules.meetings.application.ports import MeetingFilter, MeetingVisibility
from modules.meetings.domain.meeting import (
    ConfirmationSource,
    Meeting,
    MeetingFormat,
    MeetingLink,
    MeetingStatus,
    Participant,
    ParticipantResponse,
    SubjectType,
)
from modules.meetings.infrastructure.models import MeetingRecord
from shared.domain.errors import ConflictError


def _participants_to_json(participants: tuple[Participant, ...]) -> list[dict[str, Any]]:
    return [
        {
            "name": p.name,
            "email": p.email,
            "user_id": str(p.user_id) if p.user_id else None,
            "response": p.response.value,
        }
        for p in participants
    ]


def _participants_from_json(raw: list[dict[str, Any]]) -> tuple[Participant, ...]:
    return tuple(
        Participant(
            name=item["name"],
            email=item.get("email"),
            user_id=UUID(item["user_id"]) if item.get("user_id") else None,
            response=ParticipantResponse(item.get("response", ParticipantResponse.NEEDS_ACTION)),
        )
        for item in raw
    )


def _to_domain(row: MeetingRecord) -> Meeting:
    link = (
        MeetingLink(type=SubjectType(row.subject_type), id=row.subject_id)
        if row.subject_type and row.subject_id
        else None
    )
    return Meeting(
        id=row.id,
        title=row.title,
        owner_user_id=row.owner_user_id,
        scheduled_start=row.scheduled_start,
        scheduled_end=row.scheduled_end,
        status=MeetingStatus(row.status),
        description=row.description,
        location=row.location,
        link=link,
        team_id=row.team_id,
        format=MeetingFormat(row.format),
        host_user_id=row.host_user_id,
        booked_by_user_id=row.booked_by_user_id,
        confirmed_at=row.confirmed_at,
        confirmed_by_user_id=row.confirmed_by_user_id,
        confirmation_source=(
            ConfirmationSource(row.confirmation_source) if row.confirmation_source else None
        ),
        participants=_participants_from_json(row.participants),
        reminders=tuple(row.reminders),
        outcome=row.outcome,
        cancel_reason=row.cancel_reason,
        created_at=row.created_at,
        version=row.version,
    )


class DjangoMeetingRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def _qs(self) -> QuerySet[MeetingRecord]:
        return MeetingRecord.objects.using(self.using)

    def _filtered(
        self, visibility: MeetingVisibility, filters: MeetingFilter
    ) -> QuerySet[MeetingRecord]:
        qs = self._qs()
        if visibility.owner_user_id is not None:
            condition = Q(owner_user_id=visibility.owner_user_id)
            if visibility.team_ids:
                condition |= Q(team_id__in=visibility.team_ids)
            qs = qs.filter(condition)
        if filters.status is not None:
            qs = qs.filter(status=filters.status.value)
        if filters.subject_type is not None and filters.subject_id is not None:
            qs = qs.filter(subject_type=filters.subject_type.value, subject_id=filters.subject_id)
        if filters.starts_after is not None:
            qs = qs.filter(scheduled_start__gte=filters.starts_after)
        if filters.starts_before is not None:
            qs = qs.filter(scheduled_start__lt=filters.starts_before)
        return qs

    def add(self, meeting: Meeting) -> None:
        self._qs().create(**self._fields(meeting), id=meeting.id, created_at=meeting.created_at)

    def get(self, meeting_id: UUID) -> Meeting | None:
        row = self._qs().filter(id=meeting_id).first()
        return None if row is None else _to_domain(row)

    def save(self, meeting: Meeting, expected_version: int) -> None:
        updated = (
            self._qs()
            .filter(id=meeting.id, version=expected_version)
            .update(version=F("version") + 1, **self._fields(meeting))
        )
        if updated != 1:
            raise ConflictError("Meeting was modified by someone else; reload and retry.")

    def list_visible(
        self, visibility: MeetingVisibility, filters: MeetingFilter, *, limit: int, offset: int
    ) -> list[Meeting]:
        rows = self._filtered(visibility, filters).order_by("scheduled_start", "id")[
            offset : offset + limit
        ]
        return [_to_domain(row) for row in rows]

    def count(self, visibility: MeetingVisibility, filters: MeetingFilter) -> int:
        return self._filtered(visibility, filters).count()

    def _fields(self, meeting: Meeting) -> dict[str, Any]:
        return {
            "title": meeting.title,
            "description": meeting.description,
            "location": meeting.location,
            "subject_type": meeting.link.type.value if meeting.link else None,
            "subject_id": meeting.link.id if meeting.link else None,
            "status": meeting.status.value,
            "scheduled_start": meeting.scheduled_start,
            "scheduled_end": meeting.scheduled_end,
            "owner_user_id": meeting.owner_user_id,
            "team_id": meeting.team_id,
            "format": meeting.format.value,
            "host_user_id": meeting.host_user_id,
            "booked_by_user_id": meeting.booked_by_user_id,
            "confirmed_at": meeting.confirmed_at,
            "confirmed_by_user_id": meeting.confirmed_by_user_id,
            "confirmation_source": (
                meeting.confirmation_source.value if meeting.confirmation_source else None
            ),
            "participants": _participants_to_json(meeting.participants),
            "reminders": list(meeting.reminders),
            "outcome": meeting.outcome,
            "cancel_reason": meeting.cancel_reason,
        }
