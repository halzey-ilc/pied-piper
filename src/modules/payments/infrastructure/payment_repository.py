"""PaymentRepository adapter and the aggregates behind the sales metrics."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from django.db.models import (
    Case,
    DecimalField,
    F,
    Min,
    Q,
    QuerySet,
    Sum,
    When,
)

from modules.payments.application.ports import PaymentFilter, PaymentVisibility
from modules.payments.domain.payment import Payment, PaymentKind
from modules.payments.infrastructure.models import PaymentRecord
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
    )


class DjangoPaymentRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def _qs(self) -> QuerySet[PaymentRecord]:
        return PaymentRecord.objects.using(self.using)

    def _visible(self, visibility: PaymentVisibility) -> QuerySet[PaymentRecord]:
        qs = self._qs()
        if visibility.owner_user_id is None:
            return qs
        condition = Q(owner_user_id=visibility.owner_user_id)
        if visibility.team_ids:
            condition |= Q(team_id__in=visibility.team_ids)
        return qs.filter(condition)

    def _filtered(
        self, visibility: PaymentVisibility, filters: PaymentFilter
    ) -> QuerySet[PaymentRecord]:
        qs = self._visible(visibility)
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

    def sold_deal_ids(
        self, visibility: PaymentVisibility, paid_from: date, paid_to: date
    ) -> list[UUID]:
        rows = (
            self._visible(visibility)
            .filter(is_refund=False, deal_id__isnull=False)
            .values("deal_id")
            .annotate(first_paid=Min("paid_on"))
            .filter(first_paid__gte=paid_from, first_paid__lte=paid_to)
        )
        return [row["deal_id"] for row in rows]

    def received_by_deal(self, deal_ids: list[UUID]) -> dict[UUID, Decimal]:
        if not deal_ids:
            return {}
        rows = (
            self._qs().filter(deal_id__in=deal_ids).values("deal_id").annotate(total=Sum(_SIGNED))
        )
        return {row["deal_id"]: row["total"] or Decimal(0) for row in rows}
