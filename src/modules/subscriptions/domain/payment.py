"""Payments for a company's subscription, recorded on the platform. Pure domain.

Not a payment-provider integration: the platform administrator records a payment the
platform actually received (bank transfer, cash) and confirms it. Only CONFIRMED
payments count as received revenue; a recorded but unconfirmed payment does not.
Every payment carries the reference of the external operation (bank reference,
receipt number, provider id), unique per source, so the same money is never
recorded twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from shared.domain.errors import ConflictError, DomainValidationError

MAX_REFERENCE = 100
MAX_NOTE = 500


class PaymentStatus(StrEnum):
    PENDING = "pending"  # recorded, money not confirmed yet
    CONFIRMED = "confirmed"  # money received: counts as revenue
    CANCELLED = "cancelled"  # recorded by mistake / never arrived


class PaymentSource(StrEnum):
    MANUAL = "manual"  # entered by the platform administrator
    PROVIDER = "provider"  # reserved for a payment-provider integration (none yet)


class DuplicatePaymentError(ConflictError):
    code = "duplicate_payment"


class PaymentStateError(ConflictError):
    code = "payment_invalid_state"


@dataclass(slots=True)
class SubscriptionPayment:
    id: UUID
    organization_id: UUID
    subscription_id: UUID | None
    amount: Decimal
    currency: str
    paid_on: date
    reference: str
    source: PaymentSource
    status: PaymentStatus
    recorded_by: UUID
    period_start: date | None = None
    period_end: date | None = None
    note: str = ""
    decided_by: UUID | None = None
    decided_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def record(
        cls,
        *,
        organization_id: UUID,
        subscription_id: UUID | None,
        amount: Decimal,
        currency: str,
        paid_on: date,
        reference: str,
        recorded_by: UUID,
        period_start: date | None = None,
        period_end: date | None = None,
        note: str = "",
    ) -> SubscriptionPayment:
        if amount <= 0:
            raise DomainValidationError("amount must be positive.")
        if amount.as_tuple().exponent < -2:  # type: ignore[operator]
            raise DomainValidationError("amount has at most 2 decimal places.")
        if len(currency) != 3 or not currency.isalpha() or not currency.isupper():
            raise DomainValidationError("currency must be an ISO 4217 code, e.g. USD.")
        cleaned = reference.strip()
        if not cleaned:
            raise DomainValidationError("reference (the external operation id) is required.")
        if len(cleaned) > MAX_REFERENCE:
            raise DomainValidationError(f"reference must be at most {MAX_REFERENCE} characters.")
        if period_start and period_end and period_end < period_start:
            raise DomainValidationError("period_end must not be before period_start.")
        return cls(
            id=uuid4(),
            organization_id=organization_id,
            subscription_id=subscription_id,
            amount=amount,
            currency=currency,
            paid_on=paid_on,
            reference=cleaned,
            source=PaymentSource.MANUAL,
            status=PaymentStatus.PENDING,
            recorded_by=recorded_by,
            period_start=period_start,
            period_end=period_end,
            note=note.strip()[:MAX_NOTE],
        )

    def confirm(self, actor: UUID) -> None:
        self._decide(PaymentStatus.CONFIRMED, actor)

    def cancel(self, actor: UUID, note: str = "") -> None:
        self._decide(PaymentStatus.CANCELLED, actor)
        if note.strip():
            self.note = note.strip()[:MAX_NOTE]

    def _decide(self, status: PaymentStatus, actor: UUID) -> None:
        if self.status is not PaymentStatus.PENDING:
            raise PaymentStateError(f"The payment is already {self.status.value}.")
        self.status = status
        self.decided_by = actor
        self.decided_at = datetime.now(UTC)
