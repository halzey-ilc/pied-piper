"""Subscription plans and their limits. A static catalog (pure domain).

Limits map a metric name to a maximum; ``UNLIMITED`` (-1) means no cap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from shared.domain.errors import NotFoundError

UNLIMITED = -1


class Feature(StrEnum):
    """Paid modules a company can have switched on (spec: platform administration).

    The core CRM — customers, leads, deals, tasks, meetings, staff and plans — is part of
    every plan and is not listed here.
    """

    TELEPHONY = "telephony"
    COMMUNICATIONS = "communications"
    PAYMENTS = "payments"
    ANALYTICS = "analytics"
    AI = "ai"
    OPERATIONS = "operations"
    RISK = "risk"


class PlanCode(StrEnum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class PlanNotFoundError(NotFoundError):
    code = "plan_not_found"


@dataclass(frozen=True, slots=True)
class Plan:
    code: PlanCode
    name: str
    monthly_price: Decimal
    currency: str
    limits: dict[str, int] = field(default_factory=dict)
    # Modules a new subscription on this plan starts with; the platform admin may change them.
    features: frozenset[Feature] = frozenset()

    def limit_for(self, metric: str) -> int:
        """Max allowed for ``metric`` (UNLIMITED if uncapped or unknown)."""
        return self.limits.get(metric, UNLIMITED)

    def allows(self, metric: str, current: int) -> bool:
        limit = self.limit_for(metric)
        return limit == UNLIMITED or current < limit


_CATALOG: dict[PlanCode, Plan] = {
    PlanCode.FREE: Plan(
        code=PlanCode.FREE,
        name="Free",
        monthly_price=Decimal(0),
        currency="USD",
        limits={"users": 3, "customers": 100, "deals": 200},
    ),
    PlanCode.PRO: Plan(
        code=PlanCode.PRO,
        name="Pro",
        monthly_price=Decimal(49),
        currency="USD",
        limits={"users": 25, "customers": 10000, "deals": 50000},
        features=frozenset(
            {Feature.TELEPHONY, Feature.COMMUNICATIONS, Feature.PAYMENTS, Feature.ANALYTICS}
        ),
    ),
    PlanCode.ENTERPRISE: Plan(
        code=PlanCode.ENTERPRISE,
        name="Enterprise",
        monthly_price=Decimal(499),
        currency="USD",
        limits={"users": UNLIMITED, "customers": UNLIMITED, "deals": UNLIMITED},
        features=frozenset(Feature),
    ),
}


def all_plans() -> tuple[Plan, ...]:
    return tuple(_CATALOG.values())


def get_plan(code: PlanCode) -> Plan:
    try:
        return _CATALOG[code]
    except KeyError:
        raise PlanNotFoundError("Unknown plan.") from None
