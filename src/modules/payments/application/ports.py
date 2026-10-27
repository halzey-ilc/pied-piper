from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from modules.payments.domain.invoice import Invoice, InvoiceStatus


@dataclass(frozen=True, slots=True)
class InvoiceVisibility:
    owner_user_id: UUID | None = None
    team_ids: frozenset[UUID] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class InvoiceFilter:
    status: InvoiceStatus | None = None
    customer_id: UUID | None = None
    deal_id: UUID | None = None


class InvoiceRepository(Protocol):
    def add(self, invoice: Invoice) -> None: ...

    def get(self, invoice_id: UUID) -> Invoice | None: ...

    def save(self, invoice: Invoice, expected_version: int) -> None: ...

    def list_visible(
        self, visibility: InvoiceVisibility, filters: InvoiceFilter, *, limit: int, offset: int
    ) -> list[Invoice]: ...

    def count(self, visibility: InvoiceVisibility, filters: InvoiceFilter) -> int: ...


class CustomerLookup(Protocol):
    def exists(self, customer_id: UUID) -> bool: ...


class DealLookup(Protocol):
    def exists(self, deal_id: UUID) -> bool: ...
