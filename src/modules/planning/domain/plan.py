"""Sales plan aggregate and its change history. Pure Python: no Django, no ORM.

A plan is a target for one metric over one period, set at the company, department
or manager level. The monthly company/department target is set by the director; a
head of sales distributes it to managers and sets their weekly and daily targets.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from shared.domain.errors import DomainValidationError

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_WEEK_RE = re.compile(r"^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$")


class PlanScope(StrEnum):
    COMPANY = "company"
    DEPARTMENT = "department"
    USER = "user"


class PlanMetric(StrEnum):
    MONTHLY_SALES = "monthly_sales"  # number of sales
    MONTHLY_TURNOVER = "monthly_turnover"  # money
    WEEKLY_LEADS = "weekly_leads"  # potential leads
    WEEKLY_PREPAYMENTS = "weekly_prepayments"  # prepayments received
    DAILY_CALLS = "daily_calls"  # calls


_GRANULARITY: dict[PlanMetric, str] = {
    PlanMetric.MONTHLY_SALES: "month",
    PlanMetric.MONTHLY_TURNOVER: "month",
    PlanMetric.WEEKLY_LEADS: "week",
    PlanMetric.WEEKLY_PREPAYMENTS: "week",
    PlanMetric.DAILY_CALLS: "day",
}


# Metrics measured in money: their plan carries a currency and its actual counts only
# payments in that currency (no conversion).
MONEY_METRICS = frozenset({PlanMetric.MONTHLY_TURNOVER})


def plan_currency(metric: PlanMetric, currency: str | None) -> str | None:
    """The currency a plan of ``metric`` stores: a 3-letter code for money, None otherwise."""
    if metric not in MONEY_METRICS:
        return None
    code = (currency or "").strip().upper()
    if not _CURRENCY_RE.match(code):
        raise DomainValidationError("Currency must be a 3-letter code, e.g. KGS or USD.")
    return code


def period_granularity(metric: PlanMetric) -> str:
    return _GRANULARITY[metric]


def period_dates(metric: PlanMetric, period: str) -> tuple[date, date]:
    """First and last calendar day (inclusive) of a plan period: a month ``YYYY-MM``,
    an ISO week ``YYYY-Www`` (Monday..Sunday) or a day ``YYYY-MM-DD``."""
    cleaned = _validate_period(metric, period)
    granularity = _GRANULARITY[metric]
    if granularity == "month":
        year, month = int(cleaned[:4]), int(cleaned[5:7])
        return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    if granularity == "week":
        monday = date.fromisocalendar(int(cleaned[:4]), int(cleaned[6:]), 1)
        return monday, monday + timedelta(days=6)
    day = date.fromisoformat(cleaned)
    return day, day


def _validate_period(metric: PlanMetric, period: str) -> str:
    granularity = _GRANULARITY[metric]
    cleaned = period.strip()
    if granularity == "month" and _MONTH_RE.match(cleaned):
        return cleaned
    if granularity == "week" and _WEEK_RE.match(cleaned):
        return cleaned
    if granularity == "day":
        try:
            date.fromisoformat(cleaned)
        except ValueError:
            pass
        else:
            return cleaned
    raise DomainValidationError(
        f"Period '{period}' does not match the {granularity} granularity of {metric.value}."
    )


@dataclass(slots=True)
class Plan:
    id: UUID
    scope: PlanScope
    scope_id: UUID | None  # department or user id; None for the whole company
    metric: PlanMetric
    period: str
    target: Decimal
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    # Money plans only (MONEY_METRICS); None for count plans.
    currency: str | None = None

    @classmethod
    def create(
        cls,
        *,
        scope: PlanScope,
        scope_id: UUID | None,
        metric: PlanMetric,
        period: str,
        target: Decimal,
        currency: str | None = None,
    ) -> Plan:
        if scope is PlanScope.COMPANY and scope_id is not None:
            raise DomainValidationError("A company plan has no scope_id.")
        if scope in (PlanScope.DEPARTMENT, PlanScope.USER) and scope_id is None:
            raise DomainValidationError(f"A {scope.value} plan needs a scope_id.")
        if target < Decimal(0):
            raise DomainValidationError("Plan target must not be negative.")
        return cls(
            id=uuid4(),
            scope=scope,
            scope_id=scope_id,
            metric=metric,
            period=_validate_period(metric, period),
            target=target,
            currency=plan_currency(metric, currency),
        )

    def set_target(self, target: Decimal, currency: str | None = None) -> bool:
        """Change the target (and, for a money plan, the currency); False if nothing changed."""
        if target < Decimal(0):
            raise DomainValidationError("Plan target must not be negative.")
        new_currency = plan_currency(self.metric, currency or self.currency)
        if target == self.target and new_currency == self.currency:
            return False
        self.target = target
        self.currency = new_currency
        self.updated_at = datetime.now(UTC)
        return True


@dataclass(slots=True)
class PlanChange:
    id: UUID
    plan_id: UUID
    author_user_id: UUID
    old_target: Decimal | None
    new_target: Decimal
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def record(
        cls,
        *,
        plan_id: UUID,
        author_user_id: UUID,
        old_target: Decimal | None,
        new_target: Decimal,
    ) -> PlanChange:
        return cls(
            id=uuid4(),
            plan_id=plan_id,
            author_user_id=author_user_id,
            old_target=old_target,
            new_target=new_target,
        )
