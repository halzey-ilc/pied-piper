"""Plan usage of the caller's company: limits (plan + overrides), counts and warnings."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal
from modules.subscriptions.application.ports import SubscriptionRepository
from modules.subscriptions.domain.plan import (
    LIMIT_METRICS,
    LIMIT_WARNING_RATIO,
    UNLIMITED,
    PlanCode,
    get_plan,
)
from modules.subscriptions.domain.subscription import Subscription

UsageCounter = Callable[[UUID], dict[str, int]]


@dataclass(frozen=True, slots=True)
class MetricUsage:
    metric: str
    used: int
    limit: int  # UNLIMITED (-1) = no cap
    state: str  # ok | warning | reached


@dataclass(frozen=True, slots=True)
class PlanUsage:
    plan_code: PlanCode
    metrics: list[MetricUsage]
    overridden: frozenset[str]


def usage_state(used: int, limit: int) -> str:
    if limit == UNLIMITED:
        return "ok"
    if used >= limit:
        return "reached"
    return "warning" if used >= limit * LIMIT_WARNING_RATIO else "ok"


def plan_usage(subscription: Subscription | None, counts: dict[str, int]) -> PlanUsage:
    code = subscription.plan_code if subscription is not None else PlanCode.FREE
    plan = get_plan(code)
    # Without a subscription the access gate enforces nothing, so nothing is capped here either.
    limits = (
        subscription.effective_limits(plan)
        if subscription is not None
        else dict.fromkeys(LIMIT_METRICS, UNLIMITED)
    )
    metrics = [
        MetricUsage(m, counts.get(m, 0), limits[m], usage_state(counts.get(m, 0), limits[m]))
        for m in LIMIT_METRICS
    ]
    overridden = frozenset((subscription.limit_overrides or {}) if subscription else {})
    return PlanUsage(plan_code=code, metrics=metrics, overridden=overridden)


class PlanUsageService:
    def __init__(
        self,
        repository: SubscriptionRepository,
        counter: UsageCounter,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._repo = repository
        self._count = counter
        self._policy = policy or AccessPolicy()

    def usage(self, principal: Principal) -> PlanUsage:
        self._policy.require(principal, Permission.SUBSCRIPTIONS_VIEW)
        subscription = self._repo.get_by_org(principal.tenant_id)
        return plan_usage(subscription, self._count(principal.tenant_id))
