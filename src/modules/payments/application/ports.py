from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from modules.payments.domain.invoice import Invoice, InvoiceStatus
from modules.payments.domain.payment import Payment, PaymentKind, PaymentStatus
from modules.payments.domain.receipt import Receipt, ReceiptInfo


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
    status: PaymentStatus | None = None


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
    """Every aggregate below (received, sales, prepayments, new clients, per deal) counts
    CONFIRMED payments only; listing and ``get`` return payments in any status."""

    def add(self, payment: Payment) -> None: ...

    def save(self, payment: Payment) -> None:
        """Persist the verification fields (status, verifier, method, reason)."""
        ...

    def deal_totals(self, deal_id: UUID, currency: str) -> DealPaymentTotals:
        """Totals of one deal's payments in ``currency`` (plus the currencies used)."""
        ...

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


@dataclass(frozen=True, slots=True)
class DealPaymentTotals:
    """Money on one deal by verification status (all time, in the deal's records)."""

    confirmed_received: Decimal  # confirmed payments minus refunds
    pending_amount: Decimal  # payments waiting for verification
    refunded: Decimal  # refunds
    payments_count: int  # payments (not refunds) in any status
    confirmed_count: int
    pending_count: int
    rejected_count: int
    prepayments_count: int  # confirmed prepayments
    currencies: tuple[str, ...]  # currencies the deal was paid in


class ReceiptRepository(Protocol):
    def add(self, receipt: Receipt) -> None: ...

    def get(self, receipt_id: UUID) -> Receipt | None: ...

    def info(self, receipt_ids: list[UUID]) -> dict[UUID, ReceiptInfo]: ...


class ReceiptCheck(StrEnum):
    MATCHED = "matched"  # the receipt proves this payment (amount, date, recipient)
    MISMATCH = "mismatch"  # the receipt contradicts the payment
    NEEDS_MANUAL_REVIEW = "needs_manual_review"  # nothing conclusive: a person checks it


@dataclass(frozen=True, slots=True)
class ReceiptVerdict:
    check: ReceiptCheck
    note: str | None = None


class ReceiptVerifier(Protocol):
    """Automatic receipt check. ``MATCHED`` confirms the payment right away (method auto);
    anything else leaves it for the head of sales / director. Adapters: see
    ``infrastructure.receipt_verifier``."""

    def verify(self, receipt: Receipt, payment: Payment) -> ReceiptVerdict: ...


class SaleCloser(Protocol):
    """Closes a deal as won on its pipeline's system «Продажа» stage (sales_pipeline)."""

    def close_deal_as_sale(self, deal_id: UUID, actor_user_id: UUID) -> bool:
        """True if the deal was closed now; False if it was already closed."""
        ...


class SellerNotifier(Protocol):
    def payment_rejected(self, payment: Payment, reason: str) -> None: ...


@dataclass(frozen=True, slots=True)
class DealSummary:
    """What the deal card shows about money (computed here, never on the frontend)."""

    deal_id: UUID
    deal_amount: Decimal
    currency: str
    confirmed_received: Decimal
    pending_amount: Decimal
    refunded: Decimal
    debt: Decimal  # deal amount minus confirmed received money, never below zero
    payments_count: int
    confirmed_count: int
    pending_count: int
    rejected_count: int
    prepayments_count: int
    # Payments in another currency than the deal's are not added up (shown as a warning).
    other_currencies: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DealRef:
    id: UUID
    amount: Decimal
    currency: str
    owner_user_id: UUID
    team_id: UUID | None
    title: str


class DealRefLookup(Protocol):
    def find(self, deal_id: UUID) -> DealRef | None: ...
