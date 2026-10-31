from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from modules.customers.domain.customer import Customer


@dataclass(frozen=True, slots=True)
class CustomerVisibility:
    """Row filter derived from the caller's RBAC scope. ``None`` owner = all customers."""

    owner_user_id: UUID | None = None
    team_ids: frozenset[UUID] = field(default_factory=frozenset)


class CustomerRepository(Protocol):
    """Persistence port. The Django adapter maps the aggregate to/from ORM rows."""

    def add(self, customer: Customer) -> None: ...

    def get(self, customer_id: UUID) -> Customer | None: ...

    def save(self, customer: Customer, expected_version: int) -> None:
        """Optimistic concurrency: ConflictError if the stored version differs."""
        ...

    def list_visible(
        self, visibility: CustomerVisibility, limit: int, offset: int, search: str | None = None
    ) -> list[Customer]: ...

    def count(self, visibility: CustomerVisibility, search: str | None = None) -> int: ...
