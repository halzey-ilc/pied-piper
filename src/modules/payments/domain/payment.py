"""Payment aggregate: one received (or refunded) money record. Pure Python.

Payments are kept as individual records so a deal can have a prepayment, later
payments, a full payment, instalments and refunds. A sale is counted from the
first non-refund payment on a deal; turnover, received money and debt are derived
from these records, never from a single mutable balance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID, uuid4

from shared.domain.errors import DomainValidationError
from shared.domain.events import DomainEvent
from shared.domain.value_objects import Money

MAX_REFERENCE_LENGTH = 100


class PaymentKind(StrEnum):
    PREPAYMENT = "prepayment"
    ADDITIONAL = "additional"
    FULL = "full"


@dataclass(frozen=True, kw_only=True)
class PaymentRegistered(DomainEvent):
    event_type: ClassVar[str] = "payments.payment_registered"
    payment_id: UUID
    deal_id: UUID | None
    amount: str
    currency: str
    is_refund: bool
    seller_user_id: UUID

    def payload(self) -> dict[str, Any]:
        return {
            "payment_id": str(self.payment_id),
            "deal_id": str(self.deal_id) if self.deal_id else None,
            "amount": self.amount,
            "currency": self.currency,
            "is_refund": self.is_refund,
            "seller_user_id": str(self.seller_user_id),
        }


@dataclass(slots=True)
class Payment:
    id: UUID
    amount: Money
    kind: PaymentKind
    paid_on: date
    seller_user_id: UUID
    owner_user_id: UUID
    deal_id: UUID | None = None
    customer_id: UUID | None = None
    is_refund: bool = False
    reference: str | None = None  # idempotency key / external reference
    team_id: UUID | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def register(
        cls,
        *,
        amount: Money,
        kind: PaymentKind,
        paid_on: date,
        seller_user_id: UUID,
        owner_user_id: UUID,
        deal_id: UUID | None = None,
        customer_id: UUID | None = None,
        is_refund: bool = False,
        reference: str | None = None,
        team_id: UUID | None = None,
    ) -> Payment:
        if amount.amount <= Decimal(0):
            raise DomainValidationError("Payment amount must be positive.")
        if deal_id is None and customer_id is None:
            raise DomainValidationError("A payment must reference a deal or a customer.")
        cleaned_reference = " ".join(reference.split()) if reference else None
        if cleaned_reference and len(cleaned_reference) > MAX_REFERENCE_LENGTH:
            raise DomainValidationError(
                f"Reference must be at most {MAX_REFERENCE_LENGTH} characters."
            )
        return cls(
            id=uuid4(),
            amount=amount,
            kind=kind,
            paid_on=paid_on,
            seller_user_id=seller_user_id,
            owner_user_id=owner_user_id,
            deal_id=deal_id,
            customer_id=customer_id,
            is_refund=is_refund,
            reference=cleaned_reference,
            team_id=team_id,
        )

    @property
    def signed_amount(self) -> Decimal:
        """The amount's effect on received money: negative for a refund."""
        return -self.amount.amount if self.is_refund else self.amount.amount
