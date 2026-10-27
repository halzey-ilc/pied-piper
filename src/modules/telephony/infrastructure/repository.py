from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db.models import F, Q, QuerySet

from modules.telephony.application.ports import CallFilter, CallVisibility
from modules.telephony.domain.call import (
    Call,
    CallDirection,
    CallLink,
    CallStatus,
    SubjectType,
)
from modules.telephony.infrastructure.models import CallRecord
from shared.domain.errors import ConflictError
from shared.domain.value_objects import PhoneNumber


def _to_domain(row: CallRecord) -> Call:
    link = (
        CallLink(type=SubjectType(row.subject_type), id=row.subject_id)
        if row.subject_type and row.subject_id
        else None
    )
    return Call(
        id=row.id,
        direction=CallDirection(row.direction),
        from_number=PhoneNumber(row.from_number),
        to_number=PhoneNumber(row.to_number),
        owner_user_id=row.owner_user_id,
        status=CallStatus(row.status),
        started_at=row.started_at,
        ended_at=row.ended_at,
        duration_seconds=row.duration_seconds,
        recording_url=row.recording_url,
        external_id=row.external_id,
        link=link,
        team_id=row.team_id,
        notes=row.notes,
        created_at=row.created_at,
        version=row.version,
    )


class DjangoCallRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def _qs(self) -> QuerySet[CallRecord]:
        return CallRecord.objects.using(self.using)

    def _filtered(self, visibility: CallVisibility, filters: CallFilter) -> QuerySet[CallRecord]:
        qs = self._qs()
        if visibility.owner_user_id is not None:
            condition = Q(owner_user_id=visibility.owner_user_id)
            if visibility.team_ids:
                condition |= Q(team_id__in=visibility.team_ids)
            qs = qs.filter(condition)
        if filters.status is not None:
            qs = qs.filter(status=filters.status.value)
        if filters.direction is not None:
            qs = qs.filter(direction=filters.direction.value)
        if filters.subject_type is not None and filters.subject_id is not None:
            qs = qs.filter(subject_type=filters.subject_type.value, subject_id=filters.subject_id)
        return qs

    def add(self, call: Call) -> None:
        self._qs().create(id=call.id, created_at=call.created_at, **self._fields(call))

    def get(self, call_id: UUID) -> Call | None:
        row = self._qs().filter(id=call_id).first()
        return None if row is None else _to_domain(row)

    def save(self, call: Call, expected_version: int) -> None:
        updated = (
            self._qs()
            .filter(id=call.id, version=expected_version)
            .update(version=F("version") + 1, **self._fields(call))
        )
        if updated != 1:
            raise ConflictError("Call was modified by someone else; reload and retry.")

    def list_visible(
        self, visibility: CallVisibility, filters: CallFilter, *, limit: int, offset: int
    ) -> list[Call]:
        rows = self._filtered(visibility, filters).order_by("-created_at", "id")[
            offset : offset + limit
        ]
        return [_to_domain(row) for row in rows]

    def count(self, visibility: CallVisibility, filters: CallFilter) -> int:
        return self._filtered(visibility, filters).count()

    def _fields(self, call: Call) -> dict[str, Any]:
        return {
            "direction": call.direction.value,
            "from_number": str(call.from_number),
            "to_number": str(call.to_number),
            "status": call.status.value,
            "started_at": call.started_at,
            "ended_at": call.ended_at,
            "duration_seconds": call.duration_seconds,
            "recording_url": call.recording_url,
            "external_id": call.external_id,
            "subject_type": call.link.type.value if call.link else None,
            "subject_id": call.link.id if call.link else None,
            "owner_user_id": call.owner_user_id,
            "team_id": call.team_id,
            "notes": call.notes,
        }
