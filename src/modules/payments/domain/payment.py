"""Payment aggregate: one received (or refunded) money record. Pure Python.

Payments are kept as individual records so a deal can have a prepayment, later
payments, a full payment, instalments and refunds. A sale is counted from the
first confirmed non-refund payment on a deal; turnover, received money and debt are
derived from these records, never from a single mutable balance.

Verification (owner decision of 03.11.2026): a payment arrives with its receipt as
``pending_verification`` and counts in sales, KPI, plans and reports only once it is
``confirmed`` — automatically by a receipt verifier, or by the head of sales / director.
A ``rejected`` payment stays in the history with its reason and never counts. Refunds are
recorded separately (``is_refund``) and are confirmed when recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID, uuid4

from shared.domain.errors import ConflictError, DomainValidationError
from shared.domain.events import DomainEvent
from shared.domain.value_objects import Money

MAX_REFERENCE_LENGTH = 100
MAX_REJECTION_REASON_LENGTH = 500


class PaymentKind(StrEnum):
    PREPAYMENT = "prepayment"
    ADDITIONAL = "additional"
    FULL = "full"


class PaymentStatus(StrEnum):
    PENDING_VERIFICATION = "pending_verification"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class VerificationMethod(StrEnum):
    AUTO = "auto"  # a receipt verifier matched the receipt to the payment
    MANUAL = "manual"  # the head of sales or the director checked it (or a legacy record)


class PaymentAlreadyVerifiedError(ConflictError):
    code = "payment_already_verified"


@dataclass(frozen=True, kw_only=True)
class PaymentConfirmed(DomainEvent):
    event_type: ClassVar[str] = "payments.payment_confirmed"
    payment_id: UUID
    deal_id: UUID | None
    amount: str
    currency: str
    seller_user_id: UUID
    method: str

    def payload(self) -> dict[str, Any]:
        return {
            "payment_id": str(self.payment_id),
            "deal_id": str(self.deal_id) if self.deal_id else None,
            "amount": self.amount,
            "currency": self.currency,
            "seller_user_id": str(self.seller_user_id),
            "method": self.method,
        }


@dataclass(frozen=True, kw_only=True)
class PaymentRejected(DomainEvent):
    event_type: ClassVar[str] = "payments.payment_rejected"
    payment_id: UUID
    deal_id: UUID | None
    seller_user_id: UUID
    reason: str

    def payload(self) -> dict[str, Any]:
        return {
            "payment_id": str(self.payment_id),
            "deal_id": str(self.deal_id) if self.deal_id else None,
            "seller_user_id": str(self.seller_user_id),
            "reason": self.reason,
        }


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
    status: PaymentStatus = PaymentStatus.CONFIRMED
    receipt_id: UUID | None = None
    # Confirming this payment closes its deal as a sale (recorded from «Продажа»).
    close_sale: bool = False
    verified_at: datetime | None = None
    verified_by_user_id: UUID | None = None
    verification_method: VerificationMethod | None = None
    rejection_reason: str | None = None

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
        receipt_id: UUID | None = None,
        close_sale: bool = False,
    ) -> Payment:
        """A new payment waits for verification; a refund is confirmed by recording it."""
        if amount.amount <= Decimal(0):
            raise DomainValidationError("Payment amount must be positive.")
        if deal_id is None and customer_id is None:
            raise DomainValidationError("A payment must reference a deal or a customer.")
        cleaned_reference = " ".join(reference.split()) if reference else None
        if cleaned_reference and len(cleaned_reference) > MAX_REFERENCE_LENGTH:
            raise DomainValidationError(
                f"Reference must be at most {MAX_REFERENCE_LENGTH} characters."
            )
        if close_sale and (is_refund or deal_id is None):
            raise DomainValidationError("Only a payment on a deal can close it as a sale.")
        payment = cls(
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
            status=PaymentStatus.PENDING_VERIFICATION,
            receipt_id=receipt_id,
            close_sale=close_sale,
        )
        if is_refund:
            payment.status = PaymentStatus.CONFIRMED
            payment.verified_at = payment.created_at
            payment.verified_by_user_id = owner_user_id
            payment.verification_method = VerificationMethod.MANUAL
        return payment

    @property
    def counts(self) -> bool:
        """Whether the payment counts in money, sales, KPI, plans and reports."""
        return self.status is PaymentStatus.CONFIRMED

    def _require_pending(self) -> None:
        if self.status is not PaymentStatus.PENDING_VERIFICATION:
            raise PaymentAlreadyVerifiedError(f"The payment is already {self.status.value}.")

    def confirm(
        self, *, verifier_user_id: UUID | None, method: VerificationMethod
    ) -> PaymentConfirmed:
        self._require_pending()
        self.status = PaymentStatus.CONFIRMED
        self.verified_at = datetime.now(UTC)
        self.verified_by_user_id = verifier_user_id
        self.verification_method = method
        self.rejection_reason = None
        return PaymentConfirmed(
            payment_id=self.id,
            deal_id=self.deal_id,
            amount=str(self.amount.amount),
            currency=self.amount.currency,
            seller_user_id=self.seller_user_id,
            method=method.value,
        )

    def reject(self, *, verifier_user_id: UUID, reason: str) -> PaymentRejected:
        self._require_pending()
        cleaned = " ".join(reason.split()) if reason else ""
        if not cleaned:
            raise DomainValidationError("A rejection reason is required.")
        if len(cleaned) > MAX_REJECTION_REASON_LENGTH:
            raise DomainValidationError(
                f"The reason must be at most {MAX_REJECTION_REASON_LENGTH} characters."
            )
        self.status = PaymentStatus.REJECTED
        self.verified_at = datetime.now(UTC)
        self.verified_by_user_id = verifier_user_id
        self.verification_method = VerificationMethod.MANUAL
        self.rejection_reason = cleaned
        return PaymentRejected(
            payment_id=self.id,
            deal_id=self.deal_id,
            seller_user_id=self.seller_user_id,
            reason=cleaned,
        )

    @property
    def signed_amount(self) -> Decimal:
        """The amount's effect on received money: negative for a refund."""
        return -self.amount.amount if self.is_refund else self.amount.amount
