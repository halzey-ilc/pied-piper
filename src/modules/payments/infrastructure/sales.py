"""The single source of truth for SALES and NEW CLIENTS, shared by every report
(payments metrics, the head-of-sales cabinet, plan progress). Owner decision of
02.11.2026: every new deal is a separate sale, even for a client who bought before.

SALE — a DEAL whose first payment (refunds excluded) falls within the period. It is
credited to the seller of that first payment as recorded in the payment register.
- several (partial) payments of one deal: one sale, in the month of the first one;
- a second deal of the same client: a second sale;
- a deal whose payments are fully refunded (net received <= 0 in each currency it was
  paid in): not a sale;
- a lost or reopened deal keeps the sale its money made (the first payment date does
  not change on reopening, so it is never counted twice);
- a payment without a deal is money, not a sale.

NEW CLIENT — a client whose first payment ever (refunds excluded) falls within the
period, counted once. The client of a payment is its customer, else the customer of
its deal, else — for a deal without a customer — the deal itself. A separate metric:
never mixed with the number of sales, payments or the amount of money.

ONLY CONFIRMED PAYMENTS COUNT (owner decision of 03.11.2026): a payment waiting for
receipt verification or rejected is not money, not a sale and not a new client. The
sale's date is the paid-on date of the deal's first confirmed payment.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, TypedDict
from uuid import UUID

from django.db.models import (
    Case,
    DecimalField,
    F,
    Min,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Sum,
    UUIDField,
    When,
)
from django.db.models.functions import Coalesce

from modules.payments.infrastructure.models import PaymentRecord
from modules.sales_pipeline.infrastructure.models import DealRecord

_SIGNED = Case(
    When(is_refund=True, then=-F("amount")),
    default=F("amount"),
    output_field=DecimalField(max_digits=18, decimal_places=2),
)


@dataclass(frozen=True, slots=True)
class DealSale:
    deal_id: UUID
    payment_id: UUID  # the first payment of the deal
    seller_user_id: UUID
    paid_on: date


@dataclass(frozen=True, slots=True)
class NewClient:
    client_key: UUID
    payment_id: UUID
    seller_user_id: UUID
    paid_on: date


class _ClientAnnotation(TypedDict):
    client_key: UUID


if TYPE_CHECKING:  # django-stubs is a development dependency only
    from django_stubs_ext import WithAnnotations

    _Annotated = WithAnnotations[PaymentRecord, _ClientAnnotation]


def _earliest(rows: Iterable[Mapping[str, Any]], key: str) -> list[Mapping[str, Any]]:
    """First row per key from rows ordered by (key, paid_on, created_at, id)."""
    earliest: dict[Any, Mapping[str, Any]] = {}
    for row in rows:
        earliest.setdefault(row[key], row)
    return list(earliest.values())


def _restricted(
    using: str,
    rows: list[Mapping[str, Any]],
    sellers: Iterable[UUID] | None,
    restrict: Q | None,
) -> list[Mapping[str, Any]]:
    if sellers is not None:
        wanted = set(sellers)
        rows = [row for row in rows if row["seller_user_id"] in wanted]
    if restrict is not None and rows:
        allowed = set(
            PaymentRecord.counted.using(using)
            .filter(id__in=[row["id"] for row in rows])
            .filter(restrict)
            .values_list("id", flat=True)
        )
        rows = [row for row in rows if row["id"] in allowed]
    return rows


def deal_sales(
    using: str,
    start: date,
    end: date,
    *,
    sellers: Iterable[UUID] | None = None,
    restrict: Q | None = None,
) -> list[DealSale]:
    """Sales of the period. ``sellers`` keeps those credited to these users; ``restrict``
    keeps those whose first payment matches a row filter (e.g. the caller's visibility)."""
    payments = PaymentRecord.counted.using(using).filter(deal_id__isnull=False)
    sold = [
        row["deal_id"]
        for row in payments.filter(is_refund=False)
        .values("deal_id")
        .annotate(first=Min("paid_on"))
        .filter(first__gte=start, first__lte=end)
    ]
    if not sold:
        return []
    # Fully refunded: in every currency the deal was paid in, refunds cancel the payments.
    nets: dict[UUID, list[Decimal]] = {}
    for row in (
        payments.filter(deal_id__in=sold).values("deal_id", "currency").annotate(net=Sum(_SIGNED))
    ):
        nets.setdefault(row["deal_id"], []).append(row["net"] or Decimal(0))
    refunded = {deal for deal, values in nets.items() if all(v <= 0 for v in values)}
    firsts = _earliest(
        payments.filter(deal_id__in=[d for d in sold if d not in refunded], is_refund=False)
        .order_by("deal_id", "paid_on", "created_at", "id")
        .values("deal_id", "id", "seller_user_id", "paid_on"),
        "deal_id",
    )
    return [
        DealSale(
            deal_id=row["deal_id"],
            payment_id=row["id"],
            seller_user_id=row["seller_user_id"],
            paid_on=row["paid_on"],
        )
        for row in _restricted(using, firsts, sellers, restrict)
    ]


def _with_client(using: str) -> QuerySet[_Annotated]:
    deal_customer = DealRecord.objects.using(using).filter(id=OuterRef("deal_id"))
    return (
        PaymentRecord.counted.using(using)
        .filter(is_refund=False)
        .annotate(
            client_key=Coalesce(
                "customer_id",
                Subquery(deal_customer.values("customer_id")[:1]),
                "deal_id",
                output_field=UUIDField(),
            )
        )
        .filter(client_key__isnull=False)
    )


def new_clients(
    using: str,
    start: date,
    end: date,
    *,
    sellers: Iterable[UUID] | None = None,
    restrict: Q | None = None,
) -> list[NewClient]:
    """Clients whose first payment ever falls within the period (each counted once)."""
    payments = _with_client(using)
    keys = [
        row["client_key"]
        for row in payments.values("client_key")
        .annotate(first=Min("paid_on"))
        .filter(first__gte=start, first__lte=end)
    ]
    if not keys:
        return []
    firsts = _earliest(
        payments.filter(Q(client_key__in=keys))
        .order_by("client_key", "paid_on", "created_at", "id")
        .values("client_key", "id", "seller_user_id", "paid_on"),
        "client_key",
    )
    return [
        NewClient(
            client_key=row["client_key"],
            payment_id=row["id"],
            seller_user_id=row["seller_user_id"],
            paid_on=row["paid_on"],
        )
        for row in _restricted(using, firsts, sellers, restrict)
    ]
