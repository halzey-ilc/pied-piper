"""Plan-fact domain: a manager's monthly sales plan versus the actual figures, with
a pace forecast. Pure Python. Distinct numbers (count vs money), never one score."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID


@dataclass(slots=True)
class PlanFactRow:
    user_id: UUID
    username: str
    department_id: UUID | None
    product_role: str
    plan_sales: Decimal  # target number of sales for the month
    sales_actual: int  # deals whose first payment this manager took this month
    received_actual: Decimal  # money this manager received this month (refunds netted)
    working_days_total: int
    working_days_elapsed: int

    @property
    def remaining_sales(self) -> Decimal:
        return self.plan_sales - Decimal(self.sales_actual)

    @property
    def forecast_sales(self) -> Decimal:
        """Linear pace forecast: actual / elapsed * total working days."""
        if self.working_days_elapsed <= 0:
            return Decimal(self.sales_actual)
        pace = Decimal(self.sales_actual) / Decimal(self.working_days_elapsed)
        return (pace * Decimal(self.working_days_total)).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )


@dataclass(slots=True)
class PlanFact:
    month: str
    working_days_total: int
    working_days_elapsed: int
    rows: list[PlanFactRow]

    @property
    def plan_sales_total(self) -> Decimal:
        return sum((r.plan_sales for r in self.rows), Decimal(0))

    @property
    def sales_actual_total(self) -> int:
        return sum(r.sales_actual for r in self.rows)

    @property
    def received_total(self) -> Decimal:
        return sum((r.received_actual for r in self.rows), Decimal(0))

    @property
    def forecast_sales_total(self) -> Decimal:
        return sum((r.forecast_sales for r in self.rows), Decimal(0))
