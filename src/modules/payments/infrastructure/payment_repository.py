"""PaymentRepository adapter and the aggregates behind the sales metrics.

Aggregates read CONFIRMED payments only (``PaymentRecord.counted``); a payment waiting
for verification or rejected never counts as money, a sale or a prepayment."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from django.db.models import (
    Case,
    Count,
    DecimalField,
    F,
    Q,
    QuerySet,
    Sum,
    When,
)

from modules.payments.application.ports import (
    DealPaymentTotals,
    PaymentFilter,
    PaymentVisibility,
)
from modules.payments.domain.payment import (
    Payment,
    PaymentKind,
    PaymentStatus,
    VerificationMethod,
)
from modules.payments.domain.receipt import Receipt, ReceiptInfo
from modules.payments.infrastructure.models import PaymentReceiptRecord, PaymentRecord
from modules.payments.infrastructure.sales import deal_sales, new_clients
from shared.domain.value_objects import Money

# Signed amount: a refund subtracts from received money.
_SIGNED = Case(
    When(is_refund=True, then=-F("amount")),
    default=F("amount"),
    output_field=DecimalField(max_digits=18, decimal_places=2),
)


def _to_domain(row: PaymentRecord) -> Payment:
    return Payment(
        id=row.id,
        amount=Money(row.amount, row.currency),
        kind=PaymentKind(row.kind),
        paid_on=row.paid_on,
        seller_user_id=row.seller_user_id,
        owner_user_id=row.owner_user_id,
        deal_id=row.deal_id,
        customer_id=row.customer_id,
        is_refund=row.is_refund,
        reference=row.reference,
        team_id=row.team_id,
        created_at=row.created_at,
        status=PaymentStatus(row.status),
        receipt_id=row.receipt_id,
        close_sale=row.close_sale,
        verified_at=row.verified_at,
        verified_by_user_id=row.verified_by_user_id,
        verification_method=(
            VerificationMethod(row.verification_method) if row.verification_method else None
        ),
        rejection_reason=row.rejection_reason,
    )


class DjangoPaymentRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def _qs(self) -> QuerySet[PaymentRecord]:
        return PaymentRecord.objects.using(self.using)

    def _counted(self) -> QuerySet[PaymentRecord]:
        return PaymentRecord.counted.using(self.using)

    def _visible(
        self, visibility: PaymentVisibility, *, counted: bool = True
    ) -> QuerySet[PaymentRecord]:
        qs = self._counted() if counted else self._qs()
        if visibility.department_id is not None:
            qs = qs.filter(team_id=visibility.department_id)
        if visibility.owner_user_id is None:
            return qs
        condition = Q(owner_user_id=visibility.owner_user_id)
        if visibility.team_ids:
            condition |= Q(team_id__in=visibility.team_ids)
        return qs.filter(condition)

    def _filtered(
        self, visibility: PaymentVisibility, filters: PaymentFilter
    ) -> QuerySet[PaymentRecord]:
        qs = self._visible(visibility, counted=False)
        if filters.status is not None:
            qs = qs.filter(status=filters.status.value)
        if filters.deal_id is not None:
            qs = qs.filter(deal_id=filters.deal_id)
        if filters.customer_id is not None:
            qs = qs.filter(customer_id=filters.customer_id)
        if filters.seller_user_id is not None:
            qs = qs.filter(seller_user_id=filters.seller_user_id)
        if filters.kind is not None:
            qs = qs.filter(kind=filters.kind.value)
        if filters.is_refund is not None:
            qs = qs.filter(is_refund=filters.is_refund)
        if filters.paid_from is not None:
            qs = qs.filter(paid_on__gte=filters.paid_from)
        if filters.paid_to is not None:
            qs = qs.filter(paid_on__lte=filters.paid_to)
        return qs

    def add(self, payment: Payment) -> None:
        self._qs().create(
            id=payment.id,
            amount=payment.amount.amount,
            currency=payment.amount.currency,
            kind=payment.kind.value,
            paid_on=payment.paid_on,
            seller_user_id=payment.seller_user_id,
            deal_id=payment.deal_id,
            customer_id=payment.customer_id,
            is_refund=payment.is_refund,
            reference=payment.reference,
            owner_user_id=payment.owner_user_id,
            team_id=payment.team_id,
            created_at=payment.created_at,
            status=payment.status.value,
            receipt_id=payment.receipt_id,
            close_sale=payment.close_sale,
            verified_at=payment.verified_at,
            verified_by_user_id=payment.verified_by_user_id,
            verification_method=(
                payment.verification_method.value if payment.verification_method else None
            ),
            rejection_reason=payment.rejection_reason,
        )

    def save(self, payment: Payment) -> None:
        self._qs().filter(id=payment.id).update(
            status=payment.status.value,
            verified_at=payment.verified_at,
            verified_by_user_id=payment.verified_by_user_id,
            verification_method=(
                payment.verification_method.value if payment.verification_method else None
            ),
            rejection_reason=payment.rejection_reason,
        )

    def deal_totals(self, deal_id: UUID, currency: str) -> DealPaymentTotals:
        rows = self._qs().filter(deal_id=deal_id)
        confirmed = Q(status=PaymentStatus.CONFIRMED.value)
        paid = Q(is_refund=False)
        same = Q(currency=currency)
        agg = rows.aggregate(
            received=Sum("amount", filter=confirmed & paid & same),
            refunded=Sum("amount", filter=confirmed & Q(is_refund=True) & same),
            pending=Sum(
                "amount", filter=Q(status=PaymentStatus.PENDING_VERIFICATION.value) & paid & same
            ),
            payments=Count("id", filter=paid),
            confirmed_n=Count("id", filter=confirmed & paid),
            pending_n=Count("id", filter=Q(status=PaymentStatus.PENDING_VERIFICATION.value) & paid),
            rejected_n=Count("id", filter=Q(status=PaymentStatus.REJECTED.value) & paid),
            prepayments=Count("id", filter=confirmed & paid & Q(kind="prepayment")),
        )
        refunded = agg["refunded"] or Decimal(0)
        return DealPaymentTotals(
            confirmed_received=(agg["received"] or Decimal(0)) - refunded,
            pending_amount=agg["pending"] or Decimal(0),
            refunded=refunded,
            payments_count=agg["payments"] or 0,
            confirmed_count=agg["confirmed_n"] or 0,
            pending_count=agg["pending_n"] or 0,
            rejected_count=agg["rejected_n"] or 0,
            prepayments_count=agg["prepayments"] or 0,
            currencies=tuple(
                sorted(
                    set(
                        rows.exclude(status=PaymentStatus.REJECTED.value).values_list(
                            "currency", flat=True
                        )
                    )
                )
            ),
        )

    def get(self, payment_id: UUID) -> Payment | None:
        row = self._qs().filter(id=payment_id).first()
        return None if row is None else _to_domain(row)

    def get_by_reference(self, reference: str) -> Payment | None:
        row = self._qs().filter(reference=reference).first()
        return None if row is None else _to_domain(row)

    def list_visible(
        self, visibility: PaymentVisibility, filters: PaymentFilter, *, limit: int, offset: int
    ) -> list[Payment]:
        rows = self._filtered(visibility, filters).order_by("-paid_on", "-created_at", "id")[
            offset : offset + limit
        ]
        return [_to_domain(row) for row in rows]

    def count(self, visibility: PaymentVisibility, filters: PaymentFilter) -> int:
        return self._filtered(visibility, filters).count()

    def received_total(
        self, visibility: PaymentVisibility, paid_from: date, paid_to: date
    ) -> Decimal:
        qs = self._visible(visibility).filter(paid_on__gte=paid_from, paid_on__lte=paid_to)
        total = qs.aggregate(total=Sum(_SIGNED))["total"]
        return total if total is not None else Decimal(0)

    @staticmethod
    def _restrict(visibility: PaymentVisibility) -> Q | None:
        department = Q(team_id=visibility.department_id) if visibility.department_id else None
        if visibility.owner_user_id is None:
            return department
        restrict = Q(owner_user_id=visibility.owner_user_id)
        if visibility.team_ids:
            restrict |= Q(team_id__in=visibility.team_ids)
        return restrict & department if department is not None else restrict

    def prepayments(
        self, visibility: PaymentVisibility, paid_from: date, paid_to: date
    ) -> tuple[int, Decimal]:
        qs = self._visible(visibility).filter(
            kind="prepayment", is_refund=False, paid_on__gte=paid_from, paid_on__lte=paid_to
        )
        row = qs.aggregate(count=Count("id"), total=Sum("amount"))
        return row["count"] or 0, row["total"] if row["total"] is not None else Decimal(0)

    def sold_deal_ids(
        self, visibility: PaymentVisibility, paid_from: date, paid_to: date
    ) -> list[UUID]:
        """Sales of the range (payments.infrastructure.sales.deal_sales)."""
        sales = deal_sales(self.using, paid_from, paid_to, restrict=self._restrict(visibility))
        return [sale.deal_id for sale in sales]

    def new_clients_count(
        self, visibility: PaymentVisibility, paid_from: date, paid_to: date
    ) -> int:
        """Clients whose first payment ever falls in the range (counted once)."""
        return len(new_clients(self.using, paid_from, paid_to, restrict=self._restrict(visibility)))

    def per_seller(
        self, visibility: PaymentVisibility, paid_from: date, paid_to: date
    ) -> dict[UUID, tuple[int, int]]:
        restrict = self._restrict(visibility)
        out: dict[UUID, tuple[int, int]] = {}
        for sale in deal_sales(self.using, paid_from, paid_to, restrict=restrict):
            sales, clients = out.get(sale.seller_user_id, (0, 0))
            out[sale.seller_user_id] = (sales + 1, clients)
        for client in new_clients(self.using, paid_from, paid_to, restrict=restrict):
            sales, clients = out.get(client.seller_user_id, (0, 0))
            out[client.seller_user_id] = (sales, clients + 1)
        return out

    def received_by_deal(self, deal_ids: list[UUID]) -> dict[UUID, Decimal]:
        if not deal_ids:
            return {}
        rows = (
            self._counted()
            .filter(deal_id__in=deal_ids)
            .values("deal_id")
            .annotate(total=Sum(_SIGNED))
        )
        return {row["deal_id"]: row["total"] or Decimal(0) for row in rows}


class DjangoReceiptRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def add(self, receipt: Receipt) -> None:
        PaymentReceiptRecord.objects.using(self.using).create(
            id=receipt.id,
            content=receipt.content,
            content_type=receipt.content_type,
            size=receipt.size,
            filename=receipt.filename,
            sha256=receipt.sha256,
            uploaded_by_user_id=receipt.uploaded_by_user_id,
            uploaded_at=receipt.uploaded_at,
        )

    def get(self, receipt_id: UUID) -> Receipt | None:
        row = PaymentReceiptRecord.objects.using(self.using).filter(id=receipt_id).first()
        if row is None:
            return None
        return Receipt(
            id=row.id,
            content=bytes(row.content),
            content_type=row.content_type,
            size=row.size,
            filename=row.filename,
            sha256=row.sha256,
            uploaded_by_user_id=row.uploaded_by_user_id,
            uploaded_at=row.uploaded_at,
        )

    def info(self, receipt_ids: list[UUID]) -> dict[UUID, ReceiptInfo]:
        if not receipt_ids:
            return {}
        rows = (
            PaymentReceiptRecord.objects.using(self.using)
            .filter(id__in=receipt_ids)
            .defer("content")
        )
        return {
            row.id: ReceiptInfo(
                id=row.id,
                content_type=row.content_type,
                size=row.size,
                filename=row.filename,
                sha256=row.sha256,
                uploaded_by_user_id=row.uploaded_by_user_id,
                uploaded_at=row.uploaded_at,
            )
            for row in rows
        }
