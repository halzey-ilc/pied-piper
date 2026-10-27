from __future__ import annotations

from typing import Protocol
from uuid import UUID

from modules.subscriptions.domain.subscription import Subscription


class SubscriptionRepository(Protocol):
    def get_by_org(self, organization_id: UUID) -> Subscription | None: ...

    def add(self, subscription: Subscription) -> None: ...

    def save(self, subscription: Subscription, expected_version: int) -> None: ...
