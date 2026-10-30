"""Payment use cases. Authorization (RBAC + ownership scope) enforced here.

A sale is counted from a deal's first non-refund payment; turnover, received money
and debt are derived from the individual payment records and the deals' values.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from modules.access_control.domain.rbac import (
    AccessPolicy,
    Permission,
    Principal,
    ResourceOwnership,
    Scope,
)
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.payments.application.ports import (
    CustomerLookup,
    DealLookup,
    DealValueLookup,
    PaymentFilter,
    PaymentRepository,
    PaymentVisibility,
    SalesMetrics,
)
from modules.payments.domain.payment import Payment, PaymentKind, PaymentRegistered
from platform_core.application.ports import EventOutbox
from shared.application.ports import UnitOfWork
from shared.domain.errors import NotFoundError
from shared.domain.value_objects import Money

AGGREGATE = "payment"


@dataclass(frozen=True, slots=True)
class RegisterPayment:
    amount: Decimal
    currency: str
    kind: PaymentKind
    paid_on: date
    deal_id: UUID | None = None
    customer_id: UUID | None = None
    seller_user_id: UUID | None = None  # defaults to the recorder
    is_refund: bool = False
    reference: str | None = None


@dataclass(frozen=True, slots=True)
class PaymentPage:
    items: list[Payment]
    total: int


class PaymentService:
    def __init__(
        self,
        payments: PaymentRepository,
        deals: DealLookup,
        customers: CustomerLookup,
        deal_values: DealValueLookup,
        uow: UnitOfWork,
        outbox: EventOutbox,
        audit: AuditLog,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._payments = payments
        self._deals = deals
        self._customers = customers
        self._deal_values = deal_values
        self._uow = uow
        self._outbox = outbox
        self._audit = audit
        self._policy = policy or AccessPolicy()

    # --- reads --------------------------------------------------------------

    def get(self, principal: Principal, payment_id: UUID) -> Payment:
        payment = self._payments.get(payment_id)
        if payment is None or not self._policy.allows(
            principal,
            Permission.PAYMENTS_VIEW,
            _ownership(payment),
        ):
            raise NotFoundError("Payment not found.")
        return payment

    def list_payments(
        self, principal: Principal, filters: PaymentFilter, *, limit: int, offset: int
    ) -> PaymentPage:
        scope = self._policy.require(principal, Permission.PAYMENTS_VIEW)
        visibility = self._visibility(principal, scope)
        return PaymentPage(
            items=self._payments.list_visible(visibility, filters, limit=limit, offset=offset),
            total=self._payments.count(visibility, filters),
        )

    def metrics(
        self, principal: Principal, *, paid_from: date, paid_to: date, currency: str
    ) -> SalesMetrics:
        scope = self._policy.require(principal, Permission.PAYMENTS_VIEW)
        visibility = self._visibility(principal, scope)
        received = self._payments.received_total(visibility, paid_from, paid_to)
        sold = self._payments.sold_deal_ids(visibility, paid_from, paid_to)
        values = self._deal_values.values(sold)
        received_by_deal = self._payments.received_by_deal(sold)
        turnover = sum(values.values(), Decimal(0))
        debt = sum(
            (values.get(d, Decimal(0)) - received_by_deal.get(d, Decimal(0)) for d in sold),
            Decimal(0),
        )
        return SalesMetrics(
            sales_count=len(sold),
            turnover=turnover,
            received=received,
            debt=debt,
            currency=currency,
        )

    def _visibility(self, principal: Principal, scope: Scope) -> PaymentVisibility:
        if scope is Scope.ALL:
            return PaymentVisibility()
        if scope is Scope.TEAM:
            return PaymentVisibility(owner_user_id=principal.user_id, team_ids=principal.team_ids)
        return PaymentVisibility(owner_user_id=principal.user_id)

    # --- writes -------------------------------------------------------------

    def register(self, principal: Principal, command: RegisterPayment) -> Payment:
        self._policy.require(principal, Permission.PAYMENTS_CREATE)
        with self._uow:
            if command.reference is not None:
                existing = self._payments.get_by_reference(command.reference)
                if existing is not None:
                    return existing  # idempotent: the same reference is registered once
            if command.deal_id is not None and not self._deals.exists(command.deal_id):
                raise NotFoundError("Linked deal not found.")
            if command.customer_id is not None and not self._customers.exists(command.customer_id):
                raise NotFoundError("Linked customer not found.")
            payment = Payment.register(
                amount=Money(command.amount, command.currency),
                kind=command.kind,
                paid_on=command.paid_on,
                seller_user_id=command.seller_user_id or principal.user_id,
                owner_user_id=principal.user_id,
                deal_id=command.deal_id,
                customer_id=command.customer_id,
                is_refund=command.is_refund,
                reference=command.reference,
                team_id=principal.department_id,
            )
            self._payments.add(payment)
            self._outbox.add(
                PaymentRegistered(
                    payment_id=payment.id,
                    deal_id=payment.deal_id,
                    amount=str(payment.amount.amount),
                    currency=payment.amount.currency,
                    is_refund=payment.is_refund,
                    seller_user_id=payment.seller_user_id,
                ),
                aggregate_type=AGGREGATE,
                aggregate_id=str(payment.id),
            )
            self._audit.record(
                AuditEntry(
                    actor_user_id=principal.user_id,
                    action="payment.registered",
                    resource_type=AGGREGATE,
                    resource_id=str(payment.id),
                )
            )
        return payment


def _ownership(payment: Payment) -> ResourceOwnership:
    return ResourceOwnership(owner_user_id=payment.owner_user_id, team_id=payment.team_id)
