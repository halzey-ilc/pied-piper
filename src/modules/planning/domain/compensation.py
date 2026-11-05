"""A member's pay terms: a fixed monthly salary plus a percentage of the money they bring in.
Pure Python.

Owner decision of 05.11.2026: «Мой заработок за месяц» = salary + percent of the CONFIRMED
money the manager took in the month (prepayments, additional and full payments; refunds
subtracted). The director sets the terms for each employee; the amounts are in the company
currency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from shared.domain.errors import DomainValidationError

MAX_PERCENT = Decimal(100)
_CENT = Decimal("0.01")


@dataclass(slots=True)
class Compensation:
    user_id: UUID
    base_salary: Decimal
    commission_percent: Decimal
    currency: str
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.base_salary < 0:
            raise DomainValidationError("Base salary cannot be negative.")
        if not Decimal(0) <= self.commission_percent <= MAX_PERCENT:
            raise DomainValidationError("Commission percent must be between 0 and 100.")

    def commission(self, received: Decimal) -> Decimal:
        """The percentage of the money received (never negative: a month of refunds is 0)."""
        base = max(received, Decimal(0))
        return (base * self.commission_percent / MAX_PERCENT).quantize(_CENT, ROUND_HALF_UP)
