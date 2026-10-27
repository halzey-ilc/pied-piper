from __future__ import annotations

from dataclasses import dataclass, field
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


class CallRepository(Protocol):
    def add(self, call: Call) -> None: ...

    def get(self, call_id: UUID) -> Call | None: ...

    def save(self, call: Call, expected_version: int) -> None: ...

    def list_visible(
        self, visibility: CallVisibility, filters: CallFilter, *, limit: int, offset: int
    ) -> list[Call]: ...

    def count(self, visibility: CallVisibility, filters: CallFilter) -> int: ...


class SubjectChecker(Protocol):
    def exists(self, subject_type: SubjectType, subject_id: UUID) -> bool: ...


class CallDialer(Protocol):
    """Click-to-call provider. Returns the provider's call id.

    Not wired to a real telephony account; a stub returns a synthetic id. A real
    adapter would place the call through the provider's API (via integrations).
    """

    def dial(self, *, from_number: str, to_number: str) -> str: ...
