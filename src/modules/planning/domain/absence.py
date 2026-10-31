"""Staff absence aggregate. Pure Python: no Django, no ORM.

Planned absences keep the cabinet from alerting on silence (a person on holiday is
not "inactive") and from booking meetings on someone who is away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from shared.domain.errors import DomainValidationError

MAX_COMMENT_LENGTH = 500


class AbsenceKind(StrEnum):
    VACATION = "vacation"
    SICK = "sick"
    DAY_OFF = "day_off"
    BUSINESS_TRIP = "business_trip"
    NO_SHOW = "no_show"


@dataclass(slots=True)
class Absence:
    id: UUID
    user_id: UUID
    kind: AbsenceKind
    start_date: date
    end_date: date
    created_by: UUID
    comment: str | None = None
    team_id: UUID | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        *,
        user_id: UUID,
        kind: AbsenceKind,
        start_date: date,
        end_date: date,
        created_by: UUID,
        comment: str | None = None,
        team_id: UUID | None = None,
    ) -> Absence:
        if end_date < start_date:
            raise DomainValidationError("An absence must end on or after it starts.")
        cleaned = " ".join(comment.split()) if comment else None
        if cleaned and len(cleaned) > MAX_COMMENT_LENGTH:
            raise DomainValidationError(f"Comment must be at most {MAX_COMMENT_LENGTH} characters.")
        return cls(
            id=uuid4(),
            user_id=user_id,
            kind=kind,
            start_date=start_date,
            end_date=end_date,
            created_by=created_by,
            comment=cleaned,
            team_id=team_id,
        )
