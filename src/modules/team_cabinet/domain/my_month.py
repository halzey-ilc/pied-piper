"""«Мой месяц» — a sales manager's own month on «Мой день» (owner decision of 05.11.2026).
Pure Python.

Figures follow the same definitions as the rest of the product:

* SALES — deals whose first confirmed payment fell in the month, credited to its seller
  (``payments.infrastructure.sales``); TURNOVER — the amounts of those deals; DEBT — what is
  still unpaid on them; AVERAGE CHECK — turnover / sales.
* MONEY — confirmed payments the manager took in the month, by kind (prepayment, additional,
  full), refunds subtracted from the received total.
* MEETINGS — the manager's meetings dated in the month (cancelled ones excluded): assigned,
  already passed, held; «доходимость» = held / passed.
* EARNINGS — salary + percent of the money received (the director's terms); None until the
  director sets them.
* PRODUCTS — per product of the catalog shown on «Мой день»: sales, last month's sales,
  turnover, money by kind and the average check.

Money is in the company currency only: amounts in other currencies are never added.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

_CENT = Decimal("0.01")
_TENTH = Decimal("0.1")


def ratio_percent(part: Decimal | int, whole: Decimal | int) -> Decimal | None:
    """part / whole in percent with one decimal; None when there is nothing to divide by."""
    if not whole:
        return None
    return (Decimal(part) * 100 / Decimal(whole)).quantize(_TENTH, ROUND_HALF_UP)


def average(total: Decimal, count: int) -> Decimal | None:
    return (total / count).quantize(_CENT, ROUND_HALF_UP) if count else None


@dataclass(frozen=True, slots=True)
class MonthMoney:
    """Confirmed money of the month, by kind; refunds are kept apart (positive)."""

    prepayments: Decimal = Decimal(0)
    additional: Decimal = Decimal(0)
    full: Decimal = Decimal(0)
    refunds: Decimal = Decimal(0)

    @property
    def received(self) -> Decimal:
        return self.prepayments + self.additional + self.full - self.refunds


@dataclass(frozen=True, slots=True)
class SoldDeal:
    deal_id: UUID
    # In the company currency; a deal in another currency still counts as a sale but adds
    # 0 to turnover and debt (amounts in different currencies are never added).
    amount: Decimal
    paid: Decimal  # confirmed money on the deal so far (all time), refunds subtracted
    product_id: UUID | None


@dataclass(frozen=True, slots=True)
class MeetingsMonth:
    assigned: int  # dated in the month, not cancelled
    passed: int  # of those, already started
    held: int  # of those, took place

    @property
    def ahead(self) -> int:
        return max(self.assigned - self.passed, 0)

    @property
    def attendance_percent(self) -> Decimal | None:
        return ratio_percent(self.held, self.passed)


@dataclass(frozen=True, slots=True)
class Earnings:
    base_salary: Decimal
    commission_percent: Decimal
    commission: Decimal

    @property
    def total(self) -> Decimal:
        return self.base_salary + self.commission


@dataclass(frozen=True, slots=True)
class ProductMonth:
    product_id: UUID
    name: str
    sales: int
    previous_sales: int
    turnover: Decimal
    money: MonthMoney

    @property
    def average_check(self) -> Decimal | None:
        return average(self.turnover, self.sales)


@dataclass(frozen=True, slots=True)
class MyMonth:
    month: str  # YYYY-MM
    start: date
    end: date
    as_of: datetime
    currency: str
    sold: list[SoldDeal]
    previous_sales: int
    new_clients: int
    money: MonthMoney
    meetings: MeetingsMonth
    sales_target: Decimal | None
    turnover_target: Decimal | None
    earnings: Earnings | None
    products: list[ProductMonth] = field(default_factory=list)

    @property
    def sales(self) -> int:
        return len(self.sold)

    @property
    def turnover(self) -> Decimal:
        return sum((deal.amount for deal in self.sold), Decimal(0))

    @property
    def debt(self) -> Decimal:
        return sum((max(deal.amount - deal.paid, Decimal(0)) for deal in self.sold), Decimal(0))

    @property
    def average_check(self) -> Decimal | None:
        return average(self.turnover, self.sales)

    @property
    def sales_per_meeting_percent(self) -> Decimal | None:
        return ratio_percent(self.sales, self.meetings.held)
