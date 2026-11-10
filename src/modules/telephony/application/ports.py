from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from modules.telephony.domain.call import Call, CallDirection, CallStatus, SubjectType


@dataclass(frozen=True, slots=True)
class CallVisibility:
    owner_user_id: UUID | None = None
    team_ids: frozenset[UUID] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class CallFilter:
    status: CallStatus | None = None
    direction: CallDirection | None = None
    subject_type: SubjectType | None = None
    subject_id: UUID | None = None
    # Any of these statuses (e.g. missed + failed); combined with ``status`` if both set.
    statuses: frozenset[CallStatus] = frozenset()
    owner_user_id: UUID | None = None
    started_from: datetime | None = None  # inclusive
    started_to: datetime | None = None  # exclusive
    # Case-insensitive text in a phone number or the notes.
    search: str | None = None


class CallRepository(Protocol):
    def add(self, call: Call) -> None: ...

    def get(self, call_id: UUID) -> Call | None: ...

    def get_by_external_id(self, external_id: str) -> Call | None: ...

    def save(self, call: Call, expected_version: int) -> None: ...

    def list_visible(
        self, visibility: CallVisibility, filters: CallFilter, *, limit: int, offset: int
    ) -> list[Call]: ...

    def count(self, visibility: CallVisibility, filters: CallFilter) -> int: ...


class SubjectChecker(Protocol):
    def exists(self, subject_type: SubjectType, subject_id: UUID) -> bool: ...


class CallDialer(Protocol):
    """Click-to-call provider. Returns the provider's call id.

    Sipuni places the call from the owner's internal number (infrastructure.sipuni);
    without a connected PBX a stub returns a synthetic id.
    """

    def dial(
        self, *, from_number: str, to_number: str, owner_user_id: UUID | None = None
    ) -> str: ...
