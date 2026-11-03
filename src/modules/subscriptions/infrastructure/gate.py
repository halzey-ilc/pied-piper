"""TenantAccessGate adapter (port in platform_core.application.ports).

Checked on every tenant-scoped request: an expired or cancelled subscription closes
the company's business data, and a module outside the subscription is refused. The
check runs on the server for every API call, whatever the interface shows.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID

from modules.subscriptions.domain.plan import UNLIMITED, Feature, get_plan
from modules.subscriptions.domain.subscription import PlanLimitReachedError
from modules.subscriptions.infrastructure.repository import DjangoSubscriptionRepository
from modules.subscriptions.infrastructure.usage import count_usage

_API = "/api/v1/"

# URL prefix (after /api/v1/) -> the paid module it belongs to.
_FEATURE_PREFIXES: tuple[tuple[str, Feature], ...] = (
    ("telephony/", Feature.TELEPHONY),
    ("communications/", Feature.COMMUNICATIONS),
    ("payments/", Feature.PAYMENTS),
    ("analytics/", Feature.ANALYTICS),
    ("ai/", Feature.AI),
    ("operations/", Feature.OPERATIONS),
    ("risk/", Feature.RISK),
)

# Creating requests (POST, path after /api/v1/) -> the plan limit they consume.
_CREATES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^customers/$"), "customers"),
    (re.compile(r"^leads/[0-9a-f-]{36}/convert/$"), "customers"),
    (re.compile(r"^sales/deals/$"), "deals"),
    (re.compile(r"^staff/$"), "users"),
)

_LIMIT_NAMES = {"users": "accounts", "customers": "customers", "deals": "deals"}

# Reachable with an inactive subscription: its state and contacting the platform.
_ALWAYS_OPEN = ("subscription/", "support/")


def limit_for_create(path: str) -> str | None:
    tail = path.removeprefix(_API)
    return next((metric for pattern, metric in _CREATES if pattern.match(tail)), None)


def feature_for(path: str) -> Feature | None:
    tail = path.removeprefix(_API)
    return next((f for prefix, f in _FEATURE_PREFIXES if tail.startswith(prefix)), None)


class SubscriptionAccessGate:
    def __init__(self) -> None:
        self._repo = DjangoSubscriptionRepository()

    def check(
        self, tenant_id: UUID, path: str, *, method: str = "GET", db_alias: str | None = None
    ) -> None:
        if path.removeprefix(_API).startswith(_ALWAYS_OPEN):
            return
        subscription = self._repo.get_by_org(tenant_id)
        if subscription is None:
            return  # no subscription yet: the free plan is created on first access
        subscription.check_access(feature_for(path), datetime.now(UTC))
        if method == "POST" and db_alias is not None:
            metric = limit_for_create(path)
            if metric is not None:
                check_room(tenant_id, db_alias, metric, repo=self._repo)


def check_room(
    tenant_id: UUID,
    db_alias: str,
    metric: str,
    *,
    repo: DjangoSubscriptionRepository | None = None,
) -> None:
    """Raise PlanLimitReachedError when one more record of ``metric`` exceeds the plan.

    Used per request by the gate and per row by bulk creation (imports), which a single
    request-level check cannot cover."""
    subscription = (repo or DjangoSubscriptionRepository()).get_by_org(tenant_id)
    if subscription is None:
        return
    limit = subscription.effective_limits(get_plan(subscription.plan_code))[metric]
    if limit != UNLIMITED and count_usage(tenant_id, db_alias, metric)[metric] >= limit:
        raise PlanLimitReachedError(
            f"The plan's limit of {limit} {_LIMIT_NAMES[metric]} is reached. "
            "Ask the platform administrator to raise it."
        )
