"""Read-only SQL for «Мой месяц»: one manager's sales, money, meetings, pay terms and products
in a month. Reads other modules' tables in the tenant database; never writes them."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.db.models import Sum

from modules.meetings.infrastructure.models import MeetingRecord
from modules.payments.infrastructure.models import PaymentRecord
from modules.payments.infrastructure.sales import deal_sales, new_clients
from modules.planning.infrastructure.models import CompensationRecord, PlanRecord
from modules.sales_pipeline.infrastructure.models import DealRecord, ProductRecord
from modules.team_cabinet.domain.my_month import MeetingsMonth, MonthMoney, SoldDeal

# Meeting statuses meaning "the meeting took place" (completed is the legacy value).
_HELD = ("held", "completed")
_KINDS = ("prepayment", "additional", "full")


def _money(rows: dict[str, Decimal], refunds: Decimal) -> MonthMoney:
    return MonthMoney(
        prepayments=rows.get("prepayment", Decimal(0)),
        additional=rows.get("additional", Decimal(0)),
        full=rows.get("full", Decimal(0)),
        refunds=refunds,
    )


class DjangoMyMonthReader:
    def __init__(self, using: str, currency: str) -> None:
        self.using = using
        self.currency = currency

    def sold_deals(self, user_id: UUID, start: date, end: date) -> list[SoldDeal]:
        sales = deal_sales(self.using, start, end, sellers=[user_id])
        ids = [sale.deal_id for sale in sales]
        if not ids:
            return []
        deals = {
            row["id"]: row
            for row in DealRecord.objects.using(self.using)
            .filter(id__in=ids)
            .values("id", "amount", "currency", "product_id")
        }
        paid: dict[UUID, Decimal] = defaultdict(Decimal)
        for row in (
            PaymentRecord.counted.using(self.using)
            .filter(deal_id__in=ids, currency=self.currency)
            .values("deal_id", "is_refund", "amount")
        ):
            if row["deal_id"] is not None:
                paid[row["deal_id"]] += -row["amount"] if row["is_refund"] else row["amount"]
        sold: list[SoldDeal] = []
        for deal_id in ids:
            deal = deals.get(deal_id)
            same_currency = deal is not None and deal["currency"] == self.currency
            sold.append(
                SoldDeal(
                    deal_id=deal_id,
                    amount=deal["amount"] if deal is not None and same_currency else Decimal(0),
                    paid=paid.get(deal_id, Decimal(0)) if same_currency else Decimal(0),
                    product_id=deal["product_id"] if deal is not None else None,
                )
            )
        return sold

    def sales_by_product(self, user_id: UUID, start: date, end: date) -> dict[UUID | None, int]:
        sales = deal_sales(self.using, start, end, sellers=[user_id])
        products = dict(
            DealRecord.objects.using(self.using)
            .filter(id__in=[sale.deal_id for sale in sales])
            .values_list("id", "product_id")
        )
        counts: dict[UUID | None, int] = defaultdict(int)
        for sale in sales:
            counts[products.get(sale.deal_id)] += 1
        return dict(counts)

    def new_clients_count(self, user_id: UUID, start: date, end: date) -> int:
        return len(new_clients(self.using, start, end, sellers=[user_id]))

    def money(
        self, user_id: UUID, start: date, end: date
    ) -> tuple[MonthMoney, dict[UUID, MonthMoney]]:
        """The month's confirmed money taken by the user: in total and per product of the
        deal it was paid on."""
        payments = PaymentRecord.counted.using(self.using).filter(
            seller_user_id=user_id, paid_on__gte=start, paid_on__lte=end, currency=self.currency
        )
        totals: dict[str, Decimal] = {}
        refunds = Decimal(0)
        for row in payments.values("kind", "is_refund").annotate(total=Sum("amount")):
            amount = row["total"] or Decimal(0)
            if row["is_refund"]:
                refunds += amount
            elif row["kind"] in _KINDS:
                totals[row["kind"]] = totals.get(row["kind"], Decimal(0)) + amount

        deal_products: dict[UUID, UUID] = {
            deal_id: product_id
            for deal_id, product_id in DealRecord.objects.using(self.using)
            .filter(id__in=payments.filter(deal_id__isnull=False).values("deal_id"))
            .values_list("id", "product_id")
            if product_id is not None
        }
        by_product: dict[UUID, dict[str, Decimal]] = defaultdict(dict)
        product_refunds: dict[UUID, Decimal] = defaultdict(Decimal)
        for row in (
            payments.filter(deal_id__in=list(deal_products))
            .values("deal_id", "kind", "is_refund")
            .annotate(total=Sum("amount"))
        ):
            product = deal_products.get(row["deal_id"]) if row["deal_id"] else None
            if product is None:  # pragma: no cover - filtered by deal_id__in
                continue
            amount = row["total"] or Decimal(0)
            if row["is_refund"]:
                product_refunds[product] += amount
            elif row["kind"] in _KINDS:
                kinds = by_product[product]
                kinds[row["kind"]] = kinds.get(row["kind"], Decimal(0)) + amount
        products = {
            product: _money(by_product.get(product, {}), product_refunds.get(product, Decimal(0)))
            for product in set(by_product) | set(product_refunds)
        }
        return _money(totals, refunds), products

    def meetings(
        self, user_id: UUID, start: datetime, end: datetime, now: datetime
    ) -> MeetingsMonth:
        qs = (
            MeetingRecord.objects.using(self.using)
            .filter(owner_user_id=user_id, scheduled_start__gte=start, scheduled_start__lt=end)
            .exclude(status="cancelled")
        )
        return MeetingsMonth(
            assigned=qs.count(),
            passed=qs.filter(scheduled_start__lte=now).count(),
            held=qs.filter(status__in=_HELD).count(),
        )

    def targets(self, user_id: UUID, month: str) -> tuple[Decimal | None, Decimal | None]:
        """The user's monthly_sales and monthly_turnover plan targets for the month."""
        rows = dict(
            PlanRecord.objects.using(self.using)
            .filter(
                scope="user",
                scope_id=user_id,
                period=month,
                metric__in=("monthly_sales", "monthly_turnover"),
            )
            .values_list("metric", "target")
        )
        return rows.get("monthly_sales"), rows.get("monthly_turnover")

    def pay_terms(self, user_id: UUID) -> tuple[Decimal, Decimal] | None:
        """(salary, percent) set by the director, in the company currency; None if unset."""
        row = (
            CompensationRecord.objects.using(self.using)
            .filter(user_id=user_id, currency=self.currency)
            .values_list("base_salary", "commission_percent")
            .first()
        )
        return None if row is None else (row[0], row[1])

    def shown_products(self) -> list[tuple[UUID, str]]:
        """Active products the director or the head of sales put on «Мой день»."""
        return list(
            ProductRecord.objects.using(self.using)
            .filter(is_active=True, show_on_my_day=True)
            .order_by("name")
            .values_list("id", "name")
        )
