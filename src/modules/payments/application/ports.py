from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from modules.payments.domain.invoice import Invoice, InvoiceStatus
from modules.payments.domain.payment import Payment, PaymentKind


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


# --- payments ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PaymentVisibility:
    owner_user_id: UUID | None = None
    team_ids: frozenset[UUID] = field(default_factory=frozenset)
    # Narrow to one department (the record team), on top of the role's visibility.
    department_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class PaymentFilter:
    deal_id: UUID | None = None
    customer_id: UUID | None = None
    seller_user_id: UUID | None = None
    kind: PaymentKind | None = None
    is_refund: bool | None = None
    paid_from: date | None = None
    paid_to: date | None = None


@dataclass(frozen=True, slots=True)
class SalesMetrics:
    """Distinct sales figures for a period (spec §2.4), never collapsed into one."""

    sales_count: int  # SALES: deals whose first payment fell in the period (payments.sales)
    new_clients: int  # clients whose first payment ever fell in the period (counted once)
    turnover: Decimal  # total value of those sold deals
    received: Decimal  # money actually received in the period (refunds subtracted)
    debt: Decimal  # outstanding on the sold deals (turnover minus their receipts)
    currency: str
    # Prepayments received in the period (kind=prepayment, refunds excluded): count and sum.
    prepayments_count: int = 0
    prepayments_amount: Decimal = Decimal(0)
    # Per seller (only when requested): seller id -> (sales, new clients).
    by_seller: dict[UUID, tuple[int, int]] | None = None


class PaymentRepository(Protocol):
    def add(self, payment: Payment) -> None: ...

    def get(self, payment_id: UUID) -> Payment | None: ...

    def get_by_reference(self, reference: str) -> Payment | None: ...

    def list_visible(
        self, visibility: PaymentVisibility, filters: PaymentFilter, *, limit: int, offset: int
    ) -> list[Payment]: ...

    def count(self, visibility: PaymentVisibility, filters: PaymentFilter) -> int: ...

    def received_total(
        self, visibility: PaymentVisibility, paid_from: date, paid_to: date
    ) -> Decimal:
        """Signed sum (refunds subtracted) of payments with paid_on in the range."""
        ...

    def sold_deal_ids(
        self, visibility: PaymentVisibility, paid_from: date, paid_to: date
    ) -> list[UUID]:
        """Sales: deals whose first non-refund payment fell within the range and that are
        not fully refunded (each deal once)."""
        ...

    def prepayments(
        self, visibility: PaymentVisibility, paid_from: date, paid_to: date
    ) -> tuple[int, Decimal]:
        """Prepayments (not refunds) paid in the range: how many and their total."""
        ...

    def new_clients_count(
        self, visibility: PaymentVisibility, paid_from: date, paid_to: date
    ) -> int:
        """Clients whose first payment ever falls in the range (each client once)."""
        ...

    def per_seller(
        self, visibility: PaymentVisibility, paid_from: date, paid_to: date
    ) -> dict[UUID, tuple[int, int]]:
        """Seller id -> (sales, new clients) for the range, same definitions as above."""
        ...

    def received_by_deal(self, deal_ids: list[UUID]) -> dict[UUID, Decimal]:
        """All-time signed received money per deal (for debt)."""
        ...


class DealValueLookup(Protocol):
    def values(self, deal_ids: list[UUID]) -> dict[UUID, Decimal]:
        """Deal amount by id, for computing turnover and debt."""
        ...
