"""PlanActuals adapter: plan metrics computed from the company's own records.

Definitions (also in docs/API_CONTRACT.md, «Планы»):

=================== ======================================== ============================
metric              formula                                  source
=================== ======================================== ============================
monthly_sales       deals whose FIRST non-refund payment     payments (seller, team)
                    falls in the month (same rule as the
                    head-of-sales cabinet)
monthly_turnover    payments in the month minus refunds,     payments, company currency
                    company currency only                    only
weekly_leads        leads created in the week                leads (owner, team)
weekly_prepayments  prepayments (kind=prepayment, not        payments (seller, team)
                    refunds) received in the week (count)
daily_calls         calls logged on the day (activities of   activities kind=call
                    kind "call")                             (owner, team)
=================== ======================================== ============================

Payments are attributed by ``seller_user_id``; leads and calls by ``owner_user_id``.
A department plan counts the results of the department's current members (who sold,
not who registered the payment). Days are in the company's timezone.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db.models import Case, DecimalField, F, Min, QuerySet, Sum, When

from modules.activities.infrastructure.models import ActivityRecord
from modules.leads.infrastructure.models import LeadRecord
from modules.payments.infrastructure.models import PaymentRecord
from modules.planning.domain.plan import PlanMetric


class DjangoPlanActuals:
    def __init__(self, using: str) -> None:
        self.using = using

    def actual(
        self,
        metric: PlanMetric,
        start: date,
        end: date,
        *,
        user_ids: frozenset[UUID] | None,
        currency: str,
        timezone: str,
    ) -> Decimal:
        if metric is PlanMetric.MONTHLY_SALES:
            return Decimal(self._sales(start, end, user_ids))
        if metric is PlanMetric.MONTHLY_TURNOVER:
            return self._turnover(start, end, user_ids, currency)
        if metric is PlanMetric.WEEKLY_PREPAYMENTS:
            qs = self._payments(start, end, user_ids).filter(kind="prepayment", is_refund=False)
            return Decimal(qs.count())
        since, until = _instant_bounds(start, end, timezone)
        if metric is PlanMetric.WEEKLY_LEADS:
            leads = LeadRecord.objects.using(self.using).filter(
                created_at__gte=since, created_at__lt=until
            )
            return Decimal(_scoped(leads, "owner_user_id", user_ids).count())
        # PlanMetric.DAILY_CALLS
        calls = ActivityRecord.objects.using(self.using).filter(
            kind="call", occurred_at__gte=since, occurred_at__lt=until
        )
        return Decimal(_scoped(calls, "owner_user_id", user_ids).count())

    def _payments(
        self, start: date, end: date, user_ids: frozenset[UUID] | None
    ) -> QuerySet[PaymentRecord]:
        qs = PaymentRecord.objects.using(self.using).filter(paid_on__gte=start, paid_on__lte=end)
        return _scoped(qs, "seller_user_id", user_ids)

    def _turnover(
        self, start: date, end: date, user_ids: frozenset[UUID] | None, currency: str
    ) -> Decimal:
        signed = Case(
            When(is_refund=True, then=-F("amount")),
            default=F("amount"),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        )
        total = (
            self._payments(start, end, user_ids)
            .filter(currency=currency)
            .aggregate(total=Sum(signed))["total"]
        )
        return total or Decimal("0.00")

    def _sales(self, start: date, end: date, user_ids: frozenset[UUID] | None) -> int:
        payments = PaymentRecord.objects.using(self.using).filter(
            is_refund=False, deal_id__isnull=False
        )
        firsts = (
            payments.values("deal_id")
            .annotate(first=Min("paid_on"))
            .filter(first__gte=start, first__lte=end)
        )
        deal_ids = [row["deal_id"] for row in firsts]
        # The sale belongs to whoever took the deal's first payment.
        seen: set[UUID | None] = set()
        count = 0
        rows = (
            payments.filter(deal_id__in=deal_ids)
            .order_by("deal_id", "paid_on", "created_at")
            .values("deal_id", "seller_user_id")
        )
        for row in rows:
            if row["deal_id"] in seen:
                continue
            seen.add(row["deal_id"])
            if user_ids is None or row["seller_user_id"] in user_ids:
                count += 1
        return count


def _scoped[Q: QuerySet[Any]](qs: Q, user_field: str, user_ids: frozenset[UUID] | None) -> Q:
    if user_ids is None:
        return qs
    return qs.filter(**{f"{user_field}__in": user_ids})


def _instant_bounds(start: date, end: date, timezone: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(timezone)
    return (
        datetime.combine(start, time.min, tzinfo=zone),
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=zone),
    )
