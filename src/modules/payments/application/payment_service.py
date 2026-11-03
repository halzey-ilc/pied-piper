"""Payment use cases. Authorization (RBAC + ownership scope) enforced here.

A sale is counted from a deal's first CONFIRMED non-refund payment; turnover, received
money and debt are derived from the confirmed payment records and the deals' values.

Verification (owner decision of 03.11.2026): a payment is recorded with its receipt and
waits for verification. The automatic ``ReceiptVerifier`` may confirm it; otherwise the
head of sales (their own department) or the director confirms or rejects it — never the
seller or the person who recorded it. Confirming a payment recorded from the system stage
«Продажа» (``close_sale``) closes the deal as won. A rejection notifies the seller.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from uuid import UUID

from modules.access_control.domain.rbac import (
    AccessPolicy,
    Permission,
    Principal,
    ResourceOwnership,
    Scope,
    product_role,
)
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.payments.application.ports import (
    CustomerLookup,
    DealLookup,
    DealRef,
    DealRefLookup,
    DealSummary,
    DealValueLookup,
    PaymentFilter,
    PaymentRepository,
    PaymentVisibility,
    ReceiptCheck,
    ReceiptRepository,
    ReceiptVerifier,
    SaleCloser,
    SalesMetrics,
    SellerNotifier,
)
from modules.payments.domain.payment import (
    Payment,
    PaymentKind,
    PaymentRegistered,
    PaymentStatus,
    VerificationMethod,
)
from modules.payments.domain.receipt import Receipt, ReceiptInfo, ReceiptRequiredError
from platform_core.application.ports import EventOutbox
from shared.application.ports import UnitOfWork
from shared.domain.errors import AccessDeniedError, NotFoundError
from shared.domain.events import DomainEvent
from shared.domain.value_objects import Money

# Who verifies payments (product roles): the director, and the head of sales for their
# own department.
_VERIFIER_ROLES = frozenset({"director", "head_of_sales"})

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
    # Recorded from the system stage «Продажа»: confirming it closes the deal as won.
    close_sale: bool = False


@dataclass(frozen=True, slots=True)
class ReceiptUpload:
    content: bytes
    filename: str


@dataclass(frozen=True, slots=True)
class ReceiptFile:
    content: bytes
    content_type: str
    filename: str


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
        *,
        receipts: ReceiptRepository | None = None,
        verifier: ReceiptVerifier | None = None,
        deal_refs: DealRefLookup | None = None,
        sale_closer: SaleCloser | None = None,
        notifier: SellerNotifier | None = None,
    ) -> None:
        self._receipts = receipts
        self._verifier = verifier
        self._deal_refs = deal_refs
        self._sale_closer = sale_closer
        self._notifier = notifier
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
        if payment is None or not self._can_view(principal, payment):
            raise NotFoundError("Payment not found.")
        return payment

    def _can_view(self, principal: Principal, payment: Payment) -> bool:
        """Own/team/all payments by role, plus every payment of a deal the caller sees
        (the deal card shows the deal's whole payment history)."""
        if self._policy.allows(principal, Permission.PAYMENTS_VIEW, _ownership(payment)):
            return True
        return payment.deal_id is not None and self._deal_visible(principal, payment.deal_id)

    def _deal_visible(self, principal: Principal, deal_id: UUID) -> bool:
        if self._policy.scope_for(principal, Permission.PAYMENTS_VIEW) is None:
            return False
        deal = self._deal_refs.find(deal_id) if self._deal_refs else None
        return deal is not None and self._policy.allows(
            principal,
            Permission.DEALS_VIEW,
            ResourceOwnership(owner_user_id=deal.owner_user_id, team_id=deal.team_id),
        )

    def list_payments(
        self, principal: Principal, filters: PaymentFilter, *, limit: int, offset: int
    ) -> PaymentPage:
        scope = self._policy.require(principal, Permission.PAYMENTS_VIEW)
        visibility = self._visibility(principal, scope)
        if filters.deal_id is not None and self._deal_visible(principal, filters.deal_id):
            visibility = PaymentVisibility()  # the whole history of a deal the caller sees
        return PaymentPage(
            items=self._payments.list_visible(visibility, filters, limit=limit, offset=offset),
            total=self._payments.count(visibility, filters),
        )

    def metrics(
        self,
        principal: Principal,
        *,
        paid_from: date,
        paid_to: date,
        currency: str,
        by_seller: bool = False,
        department_id: UUID | None = None,
    ) -> SalesMetrics:
        scope = self._policy.require(principal, Permission.PAYMENTS_VIEW)
        visibility = self._visibility(principal, scope)
        if department_id is not None:
            # A head of sales narrows only within their own department; the director any.
            if scope is not Scope.ALL and department_id not in principal.team_ids:
                raise AccessDeniedError("You can only see your own department.")
            visibility = replace(visibility, department_id=department_id)
        prepayments_count, prepayments_amount = self._payments.prepayments(
            visibility, paid_from, paid_to
        )
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
            new_clients=self._payments.new_clients_count(visibility, paid_from, paid_to),
            turnover=turnover,
            received=received,
            debt=debt,
            currency=currency,
            prepayments_count=prepayments_count,
            prepayments_amount=prepayments_amount,
            by_seller=(
                self._payments.per_seller(visibility, paid_from, paid_to) if by_seller else None
            ),
        )

    def _visibility(self, principal: Principal, scope: Scope) -> PaymentVisibility:
        if scope is Scope.ALL:
            return PaymentVisibility()
        if scope is Scope.TEAM:
            return PaymentVisibility(owner_user_id=principal.user_id, team_ids=principal.team_ids)
        return PaymentVisibility(owner_user_id=principal.user_id)

    # --- writes -------------------------------------------------------------

    def register(
        self,
        principal: Principal,
        command: RegisterPayment,
        receipt_upload: ReceiptUpload | None = None,
    ) -> Payment:
        """Record a payment with its receipt (required for every payment; optional for a
        refund). It waits for verification unless the automatic verifier matches it."""
        self._policy.require(principal, Permission.PAYMENTS_CREATE)
        with self._uow:
            if command.reference is not None:
                existing = self._payments.get_by_reference(command.reference)
                if existing is not None:
                    return existing  # idempotent: the same reference is registered once
            if receipt_upload is None and not command.is_refund:
                raise ReceiptRequiredError(
                    "Attach the receipt (PDF or a photo): a payment counts only once it is "
                    "confirmed by its receipt."
                )
            if command.deal_id is not None and not self._deals.exists(command.deal_id):
                raise NotFoundError("Linked deal not found.")
            if command.customer_id is not None and not self._customers.exists(command.customer_id):
                raise NotFoundError("Linked customer not found.")
            receipt = (
                Receipt.upload(
                    content=receipt_upload.content,
                    filename=receipt_upload.filename,
                    uploaded_by_user_id=principal.user_id,
                )
                if receipt_upload is not None
                else None
            )
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
                receipt_id=receipt.id if receipt is not None else None,
                close_sale=command.close_sale,
            )
            if receipt is not None:
                self._require_receipts().add(receipt)
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
            self._record(principal.user_id, "payment.registered", payment)
            if receipt is not None and payment.status is PaymentStatus.PENDING_VERIFICATION:
                self._auto_verify(receipt, payment)
        return payment

    def _auto_verify(self, receipt: Receipt, payment: Payment) -> None:
        if self._verifier is None:
            return
        verdict = self._verifier.verify(receipt, payment)
        if verdict.check is ReceiptCheck.MATCHED:
            self._apply(
                payment,
                payment.confirm(verifier_user_id=None, method=VerificationMethod.AUTO),
                actor_user_id=None,
                action="payment.confirmed",
            )
            self._close_sale_if_flagged(payment, payment.owner_user_id)
        elif verdict.check is ReceiptCheck.MISMATCH:
            # Stays pending: a person decides. Audit details carry no free text.
            self._record(None, "payment.receipt_mismatch", payment)

    # --- verification (head of sales / director) ---------------------------

    def can_verify(self, principal: Principal, payment: Payment) -> bool:
        return self._verify_denial(principal, payment) is None

    def _verify_denial(self, principal: Principal, payment: Payment) -> str | None:
        if product_role(principal.role) not in _VERIFIER_ROLES:
            return "Only the head of sales or the director confirms payments."
        scope = self._policy.scope_for(principal, Permission.PAYMENTS_UPDATE)
        if scope is None or scope is Scope.OWN:
            return "Only the head of sales or the director confirms payments."
        if scope is Scope.TEAM and not self._in_department(principal, payment):
            return "You can only confirm payments of your own department."
        if principal.user_id in {payment.seller_user_id, payment.owner_user_id}:
            return "You cannot confirm a payment you made or recorded yourself."
        return None

    def review_queue(self, principal: Principal, *, limit: int, offset: int) -> PaymentPage:
        """Pending payments for the head of sales (their department) or the director."""
        if product_role(principal.role) not in _VERIFIER_ROLES:
            raise AccessDeniedError("Only the head of sales or the director checks payments.")
        return self.list_payments(
            principal,
            PaymentFilter(status=PaymentStatus.PENDING_VERIFICATION),
            limit=limit,
            offset=offset,
        )

    def _in_department(self, principal: Principal, payment: Payment) -> bool:
        """The payment belongs to the caller's department: recorded there, or on a deal of
        that department (e.g. the director recorded it for a manager's deal)."""
        if payment.team_id is not None and payment.team_id in principal.team_ids:
            return True
        if payment.deal_id is None or self._deal_refs is None:
            return False
        deal = self._deal_refs.find(payment.deal_id)
        return deal is not None and deal.team_id is not None and deal.team_id in principal.team_ids

    def _load_for_verification(self, principal: Principal, payment_id: UUID) -> Payment:
        payment = self.get(principal, payment_id)  # 404 if not visible
        denial = self._verify_denial(principal, payment)
        if denial is not None:
            raise AccessDeniedError(denial)
        return payment

    def confirm(self, principal: Principal, payment_id: UUID) -> Payment:
        """Confirm a pending payment. It now counts in sales, KPI, plans and reports; a
        payment recorded from «Продажа» closes its deal as won."""
        with self._uow:
            payment = self._load_for_verification(principal, payment_id)
            event = payment.confirm(
                verifier_user_id=principal.user_id, method=VerificationMethod.MANUAL
            )
            self._apply(payment, event, actor_user_id=principal.user_id, action="payment.confirmed")
            self._close_sale_if_flagged(payment, principal.user_id)
        return payment

    def reject(self, principal: Principal, payment_id: UUID, reason: str) -> Payment:
        """Reject a pending payment: it stays in the history with the reason, never counts,
        the deal keeps its stage, and the seller is notified."""
        with self._uow:
            payment = self._load_for_verification(principal, payment_id)
            event = payment.reject(verifier_user_id=principal.user_id, reason=reason)
            self._apply(payment, event, actor_user_id=principal.user_id, action="payment.rejected")
            if self._notifier is not None:
                self._notifier.payment_rejected(payment, event.reason)
        return payment

    def _apply(
        self, payment: Payment, event: DomainEvent, *, actor_user_id: UUID | None, action: str
    ) -> None:
        self._payments.save(payment)
        self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(payment.id))
        self._record(actor_user_id, action, payment, fields=("status",))

    def _close_sale_if_flagged(self, payment: Payment, actor_user_id: UUID) -> None:
        if payment.close_sale and payment.deal_id is not None and self._sale_closer is not None:
            self._sale_closer.close_deal_as_sale(payment.deal_id, actor_user_id)

    # --- receipts and the deal card -----------------------------------------

    def receipt(self, principal: Principal, payment_id: UUID) -> ReceiptFile:
        payment = self.get(principal, payment_id)
        stored = (
            self._require_receipts().get(payment.receipt_id)
            if payment.receipt_id is not None
            else None
        )
        if stored is None:
            raise NotFoundError("This payment has no receipt.")
        return ReceiptFile(
            content=stored.content, content_type=stored.content_type, filename=stored.filename
        )

    def receipt_info(self, payments: list[Payment]) -> dict[UUID, ReceiptInfo]:
        ids = [p.receipt_id for p in payments if p.receipt_id is not None]
        return self._receipts.info(ids) if self._receipts is not None and ids else {}

    def deals(self, deal_ids: list[UUID]) -> dict[UUID, DealRef]:
        if self._deal_refs is None:
            return {}
        refs = (self._deal_refs.find(deal_id) for deal_id in set(deal_ids))
        return {ref.id: ref for ref in refs if ref is not None}

    def deal_summary(self, principal: Principal, deal_id: UUID) -> DealSummary:
        """Deal value, confirmed money (refunds subtracted), money waiting for
        verification and the client's debt, in the deal's currency."""
        self._policy.require(principal, Permission.PAYMENTS_VIEW)
        deal = self._deal_refs.find(deal_id) if self._deal_refs else None
        if deal is None or not self._deal_visible(principal, deal_id):
            raise NotFoundError("Deal not found.")
        totals = self._payments.deal_totals(deal_id, deal.currency)
        return DealSummary(
            deal_id=deal_id,
            deal_amount=deal.amount,
            currency=deal.currency,
            confirmed_received=totals.confirmed_received,
            pending_amount=totals.pending_amount,
            refunded=totals.refunded,
            debt=max(deal.amount - totals.confirmed_received, Decimal(0)),
            payments_count=totals.payments_count,
            confirmed_count=totals.confirmed_count,
            pending_count=totals.pending_count,
            rejected_count=totals.rejected_count,
            prepayments_count=totals.prepayments_count,
            other_currencies=tuple(c for c in totals.currencies if c != deal.currency),
        )

    def _require_receipts(self) -> ReceiptRepository:
        if self._receipts is None:  # pragma: no cover - wired in composition
            raise NotFoundError("Receipts are not available.")
        return self._receipts

    def _record(
        self,
        actor_user_id: UUID | None,
        action: str,
        payment: Payment,
        *,
        fields: tuple[str, ...] = (),
        details: dict[str, str] | None = None,
    ) -> None:
        self._audit.record(
            AuditEntry(
                actor_user_id=actor_user_id,
                action=action,
                resource_type=AGGREGATE,
                resource_id=str(payment.id),
                changed_fields=fields,
                details=details or {},
            )
        )


def _ownership(payment: Payment) -> ResourceOwnership:
    return ResourceOwnership(owner_user_id=payment.owner_user_id, team_id=payment.team_id)
