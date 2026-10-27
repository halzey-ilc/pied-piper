"""Invoice aggregate. Pure Python: no Django, no ORM. No card or bank data is held.

Lifecycle:

    DRAFT ──issue──> ISSUED ──record_payment(...)──> PAID (when fully paid)
      │                 │
      └──void(reason)───┴──> VOID
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID, uuid4

from shared.domain.errors import ConflictError, DomainValidationError
from shared.domain.events import DomainEvent
from shared.domain.value_objects import Money

_NUMBER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_-]{2,31}$")


class InvoiceStatus(StrEnum):
    DRAFT = "draft"
    ISSUED = "issued"
    PAID = "paid"
    VOID = "void"


class InvoiceStateError(ConflictError):
    code = "invoice_invalid_state"


def generate_number() -> str:
    return "INV-" + uuid4().hex[:10].upper()


def _validate_number(number: str) -> str:
    cleaned = number.strip()
    if not _NUMBER_RE.match(cleaned):
        raise DomainValidationError("Invoice number must be 3-32 safe characters.")
    return cleaned


@dataclass(frozen=True, kw_only=True)
class InvoiceIssued(DomainEvent):
    event_type: ClassVar[str] = "payments.invoice_issued"
    invoice_id: UUID
    amount: str
    currency: str

    def payload(self) -> dict[str, Any]:
        return {
            "invoice_id": str(self.invoice_id),
            "amount": self.amount,
            "currency": self.currency,
        }


@dataclass(frozen=True, kw_only=True)
class PaymentReceived(DomainEvent):
    event_type: ClassVar[str] = "payments.payment_received"
    invoice_id: UUID
    amount: str
    currency: str

    def payload(self) -> dict[str, Any]:
        return {
            "invoice_id": str(self.invoice_id),
            "amount": self.amount,
            "currency": self.currency,
        }


@dataclass(frozen=True, kw_only=True)
class InvoicePaid(DomainEvent):
    event_type: ClassVar[str] = "payments.invoice_paid"
    invoice_id: UUID

    def payload(self) -> dict[str, Any]:
        return {"invoice_id": str(self.invoice_id)}


@dataclass(frozen=True, kw_only=True)
class InvoiceVoided(DomainEvent):
    event_type: ClassVar[str] = "payments.invoice_voided"
    invoice_id: UUID

    def payload(self) -> dict[str, Any]:
        return {"invoice_id": str(self.invoice_id)}


@dataclass(slots=True)
class Invoice:
    id: UUID
    number: str
    total: Money
    paid: Money
    owner_user_id: UUID
    status: InvoiceStatus = InvoiceStatus.DRAFT
    customer_id: UUID | None = None
    deal_id: UUID | None = None
    team_id: UUID | None = None
    due_date: date | None = None
    issued_at: datetime | None = None
    paid_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    version: int = 1

    @classmethod
    def draft(
        cls,
        *,
        total: Money,
        owner_user_id: UUID,
        number: str | None = None,
        customer_id: UUID | None = None,
        deal_id: UUID | None = None,
        team_id: UUID | None = None,
        due_date: date | None = None,
    ) -> Invoice:
        if total.amount <= 0:
            raise DomainValidationError("Invoice total must be positive.")
        return cls(
            id=uuid4(),
            number=_validate_number(number) if number else generate_number(),
            total=total,
            paid=Money(Decimal(0), total.currency),
            owner_user_id=owner_user_id,
            customer_id=customer_id,
            deal_id=deal_id,
            team_id=team_id,
            due_date=due_date,
        )

    @property
    def outstanding(self) -> Money:
        return Money(self.total.amount - self.paid.amount, self.total.currency)

    def issue(self) -> InvoiceIssued:
        if self.status is not InvoiceStatus.DRAFT:
            raise InvoiceStateError("Only a draft invoice can be issued.")
        self.status = InvoiceStatus.ISSUED
        self.issued_at = datetime.now(UTC)
        self.version += 1
        return InvoiceIssued(
            invoice_id=self.id, amount=str(self.total.amount), currency=self.total.currency
        )

    def record_payment(self, amount: Money) -> tuple[PaymentReceived, InvoicePaid | None]:
        if self.status not in (InvoiceStatus.ISSUED,):
            raise InvoiceStateError("Payments can only be recorded against an issued invoice.")
        if amount.currency != self.total.currency:
            raise DomainValidationError("Payment currency must match the invoice currency.")
        if amount.amount <= 0:
            raise DomainValidationError("Payment amount must be positive.")
        new_paid = self.paid.amount + amount.amount
        if new_paid > self.total.amount:
            raise DomainValidationError("Payment exceeds the outstanding amount.")
        self.paid = Money(new_paid, self.total.currency)
        received = PaymentReceived(
            invoice_id=self.id, amount=str(amount.amount), currency=amount.currency
        )
        paid_event: InvoicePaid | None = None
        if self.paid.amount == self.total.amount:
            self.status = InvoiceStatus.PAID
            self.paid_at = datetime.now(UTC)
            paid_event = InvoicePaid(invoice_id=self.id)
        self.version += 1
        return received, paid_event

    def void(self, reason: str) -> InvoiceVoided:
        if self.status in (InvoiceStatus.PAID, InvoiceStatus.VOID):
            raise InvoiceStateError(f"A {self.status.value} invoice cannot be voided.")
        if not reason.strip():
            raise DomainValidationError("A void reason is required.")
        self.status = InvoiceStatus.VOID
        self.version += 1
        return InvoiceVoided(invoice_id=self.id)
