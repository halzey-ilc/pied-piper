"""Journal of user actions in a company (read side of the audit trail).

The director (and owner/admin) sees every entry of the company; a head of sales sees
the entries made by the members of their own department. Entries are read-only: the
audit trail is append-only by design.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal, Scope


@dataclass(frozen=True, slots=True)
class JournalFilter:
    actor_user_id: UUID | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    action: str | None = None  # exact "resource.verb" or a "resource." prefix
    date_from: date | None = None
    date_to: date | None = None


@dataclass(frozen=True, slots=True)
class JournalEntry:
    id: UUID
    occurred_at: datetime
    actor_user_id: UUID | None
    action: str
    resource_type: str
    resource_id: str
    changed_fields: tuple[str, ...]
    details: dict[str, str]


class JournalReader(Protocol):
    def list(
        self,
        filters: JournalFilter,
        *,
        actor_ids: frozenset[UUID] | None,
        limit: int,
        offset: int,
    ) -> tuple[list[JournalEntry], int]: ...


# Users of one department (the head of sales' scope), resolved in the control plane.
DepartmentMembers = Callable[[UUID, UUID], frozenset[UUID]]


class AuditJournalService:
    def __init__(
        self,
        reader: JournalReader,
        department_members: DepartmentMembers,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._reader = reader
        self._members = department_members
        self._policy = policy or AccessPolicy()

    def entries(
        self, principal: Principal, filters: JournalFilter, *, limit: int, offset: int
    ) -> tuple[list[JournalEntry], int]:
        scope = self._policy.require(principal, Permission.AUDIT_VIEW)
        actor_ids: frozenset[UUID] | None = None
        if scope is not Scope.ALL:
            # A head of sales without a department sees nothing rather than everything.
            actor_ids = (
                self._members(principal.tenant_id, principal.department_id)
                if principal.department_id is not None
                else frozenset()
            )
        return self._reader.list(filters, actor_ids=actor_ids, limit=limit, offset=offset)
