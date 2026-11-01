"""TenantAccessGate adapter (port in platform_core.application.ports).

Checked on every tenant-scoped request: an expired or cancelled subscription closes
the company's business data, and a module outside the subscription is refused. The
check runs on the server for every API call, whatever the interface shows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from modules.subscriptions.domain.plan import Feature
from modules.subscriptions.infrastructure.repository import DjangoSubscriptionRepository

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

# Reachable with an inactive subscription: its state and contacting the platform.
_ALWAYS_OPEN = ("subscription/", "support/")


def feature_for(path: str) -> Feature | None:
    tail = path.removeprefix(_API)
    return next((f for prefix, f in _FEATURE_PREFIXES if tail.startswith(prefix)), None)


class SubscriptionAccessGate:
    def __init__(self) -> None:
        self._repo = DjangoSubscriptionRepository()

    def check(self, tenant_id: UUID, path: str) -> None:
        if path.removeprefix(_API).startswith(_ALWAYS_OPEN):
            return
        subscription = self._repo.get_by_org(tenant_id)
        if subscription is None:
            return  # no subscription yet: the free plan is created on first access
        subscription.check_access(feature_for(path), datetime.now(UTC))
