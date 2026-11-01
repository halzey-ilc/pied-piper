from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from modules.subscriptions.domain.payment import PaymentStatus, SubscriptionPayment
from modules.subscriptions.domain.subscription import Subscription


class SubscriptionRepository(Protocol):
    def get_by_org(self, organization_id: UUID) -> Subscription | None: ...

    def add(self, subscription: Subscription) -> None: ...

    def save(self, subscription: Subscription, expected_version: int) -> None: ...


@dataclass(frozen=True, slots=True)
class PaymentFilter:
    organization_id: UUID | None = None
    status: PaymentStatus | None = None
    paid_from: date | None = None
    paid_to: date | None = None


class SubscriptionPaymentRepository(Protocol):
    def add(self, payment: SubscriptionPayment) -> None:
        """DuplicatePaymentError if (source, reference) is already recorded."""
        ...

    def get(self, payment_id: UUID) -> SubscriptionPayment | None: ...

    def save_decision(self, payment: SubscriptionPayment) -> None:
        """Persist a status decision; PaymentStateError if it was decided concurrently."""
        ...

    def list(
        self, filters: PaymentFilter, *, limit: int, offset: int
    ) -> tuple[list[SubscriptionPayment], int]: ...

    def confirmed_totals(self, paid_from: date, paid_to: date) -> dict[str, Decimal]:
        """Sum of CONFIRMED payments paid within the dates, per currency."""
        ...
