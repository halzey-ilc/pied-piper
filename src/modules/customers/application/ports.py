from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from modules.access_control.domain.rbac import MemberRef
from modules.customers.domain.customer import Customer


@dataclass(frozen=True, slots=True)
class CustomerVisibility:
    """Row filter derived from the caller's RBAC scope. ``None`` owner = all customers."""

    owner_user_id: UUID | None = None
    team_ids: frozenset[UUID] = field(default_factory=frozenset)


CUSTOMER_ORDERING = frozenset({"name", "created_at"})


@dataclass(frozen=True, slots=True)
class CustomerFilter:
    search: str | None = None
    owner_user_id: UUID | None = None
    # False (default): active only; True: archived only; None: both.
    archived: bool | None = False
    # One of CUSTOMER_ORDERING, optionally prefixed with "-"; default newest first.
    ordering: str | None = None


class CustomerRepository(Protocol):
    """Persistence port. The Django adapter maps the aggregate to/from ORM rows."""

    def add(self, customer: Customer) -> None: ...

    def get(self, customer_id: UUID) -> Customer | None: ...

    def save(self, customer: Customer, expected_version: int) -> None:
        """Optimistic concurrency: ConflictError if the stored version differs."""
        ...

    def list_visible(
        self, visibility: CustomerVisibility, filters: CustomerFilter, limit: int, offset: int
    ) -> list[Customer]: ...

    def count(self, visibility: CustomerVisibility, filters: CustomerFilter) -> int: ...


class MemberDirectory(Protocol):
    """Active members of the company (a customer can be handed only to one of them)."""

    def active_member(self, user_id: UUID) -> MemberRef | None: ...
