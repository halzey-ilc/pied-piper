"""A member's own «Мой день» (owner decision of 05.11.2026): «Мой месяц» — sales, money,
meetings, earnings and products — and «Фокус», the prioritised list of their own records.

Unlike the cabinet, this is not a team view: every caller sees only themselves, so it needs
only the ordinary own-scope permissions (payments.view for the month, activities.view for
the focus), which sales managers have.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal
from modules.planning.domain.compensation import Compensation
from modules.team_cabinet.application.ports import ActionsReader, MyMonthReader
from modules.team_cabinet.domain.actions import (
    FOCUS_ORDER,
    ActionGroup,
    ActionKind,
    ActionThresholds,
    TodayActions,
)
from modules.team_cabinet.domain.my_month import Earnings, MonthMoney, MyMonth, ProductMonth
from shared.domain.errors import DomainValidationError


def month_bounds(month: str) -> tuple[date, date]:
    """First and last day of a YYYY-MM month."""
    try:
        year, number = (int(part) for part in month.split("-"))
        start = date(year, number, 1)
    except ValueError as exc:
        raise DomainValidationError("month must be YYYY-MM.") from exc
    return start, start.replace(day=calendar.monthrange(year, number)[1])


def _previous_month(start: date) -> tuple[date, date]:
    end = start - timedelta(days=1)
    return end.replace(day=1), end


class MyDayService:
    def __init__(
        self,
        month_reader: MyMonthReader,
        actions: ActionsReader,
        *,
        currency: str,
        timezone_name: str,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._month = month_reader
        self._actions = actions
        self._currency = currency
        self._tz = ZoneInfo(timezone_name)
        self._policy = policy or AccessPolicy()

    def my_month(self, principal: Principal, month: str | None = None) -> MyMonth:
        self._policy.require(principal, Permission.PAYMENTS_VIEW)
        now = datetime.now(self._tz)
        label = month or now.strftime("%Y-%m")
        start, end = month_bounds(label)
        user = principal.user_id
        reader = self._month

        sold = reader.sold_deals(user, start, end)
        previous_start, previous_end = _previous_month(start)
        previous_by_product = reader.sales_by_product(user, previous_start, previous_end)
        money, money_by_product = reader.money(user, start, end)
        day_start = datetime(start.year, start.month, start.day, tzinfo=self._tz)
        day_after = day_start + timedelta(days=(end - start).days + 1)
        sales_target, turnover_target = reader.targets(user, label)

        terms = reader.pay_terms(user)
        earnings = None
        if terms is not None:
            pay = Compensation(
                user_id=user,
                base_salary=terms[0],
                commission_percent=terms[1],
                currency=self._currency,
            )
            earnings = Earnings(
                base_salary=pay.base_salary,
                commission_percent=pay.commission_percent,
                commission=pay.commission(money.received),
            )

        products = [
            ProductMonth(
                product_id=product_id,
                name=name,
                sales=sum(1 for deal in sold if deal.product_id == product_id),
                previous_sales=previous_by_product.get(product_id, 0),
                turnover=sum(
                    (deal.amount for deal in sold if deal.product_id == product_id), Decimal(0)
                ),
                money=money_by_product.get(product_id, MonthMoney()),
            )
            for product_id, name in reader.shown_products()
        ]
        return MyMonth(
            month=label,
            start=start,
            end=end,
            as_of=now,
            currency=self._currency,
            sold=sold,
            previous_sales=sum(previous_by_product.values()),
            new_clients=reader.new_clients_count(user, start, end),
            money=money,
            meetings=reader.meetings(user, day_start, day_after, now),
            sales_target=sales_target,
            turnover_target=turnover_target,
            earnings=earnings,
            products=products,
        )

    def my_focus(self, principal: Principal, *, limit: int) -> TodayActions:
        """The caller's own records that need work, most important first (FOCUS_ORDER)."""
        self._policy.require(principal, Permission.ACTIVITIES_VIEW)
        owners = [principal.user_id]
        reader = self._actions
        now = datetime.now(self._tz)
        today = now.date()
        thresholds = ActionThresholds(stalled_deal_days=reader.stalled_deal_days())
        groups: dict[ActionKind, ActionGroup] = {
            ActionKind.MISS_TASKS: reader.miss_tasks(owners, today=today, limit=limit),
            ActionKind.OVERDUE_TASKS: reader.overdue_tasks(
                owners, today=today, limit=limit, skip_misses=True
            ),
            ActionKind.DEALS_TO_PUSH: reader.deals_to_push(
                owners, now=now, stalled_days=thresholds.stalled_deal_days, limit=limit
            ),
            ActionKind.DEBTS_TO_COLLECT: reader.debts_to_collect(owners, now=now, limit=limit),
            ActionKind.LEADS_WITHOUT_CONTACT: reader.leads_without_contact(
                owners, now=now, response_minutes=thresholds.lead_response_minutes, limit=limit
            ),
            ActionKind.MEETINGS_WITHOUT_OUTCOME: reader.meetings_without_outcome(
                owners, now=now, limit=limit
            ),
            ActionKind.DEALS_WITHOUT_NEXT_STEP: reader.deals_without_next_step(
                owners, now=now, limit=limit
            ),
        }
        return TodayActions(
            day=today,
            as_of=now,
            limit=limit,
            thresholds=thresholds,
            department_id=None,
            groups=[groups[kind] for kind in FOCUS_ORDER],
        )
