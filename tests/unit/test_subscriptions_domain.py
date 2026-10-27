from __future__ import annotations

from uuid import uuid4

import pytest

from modules.subscriptions.domain.plan import PlanCode, get_plan
from modules.subscriptions.domain.subscription import (
    PlanChanged,
    Subscription,
    SubscriptionCancelled,
    SubscriptionStarted,
    SubscriptionStateError,
)
from shared.domain.errors import DomainValidationError


def test_plan_catalog_limits() -> None:
    free = get_plan(PlanCode.FREE)
    assert free.limit_for("users") == 3
    assert free.allows("users", 2) and not free.allows("users", 3)
    enterprise = get_plan(PlanCode.ENTERPRISE)
    assert enterprise.allows("users", 10**9)  # unlimited


def test_start_defaults_to_free_active() -> None:
    sub, event = Subscription.start(organization_id=uuid4())
    assert sub.plan_code is PlanCode.FREE and sub.status.value == "active"
    assert isinstance(event, SubscriptionStarted)


def test_change_plan_rules() -> None:
    sub, _ = Subscription.start(organization_id=uuid4())
    event = sub.change_plan(PlanCode.PRO)
    assert isinstance(event, PlanChanged) and sub.plan_code is PlanCode.PRO
    with pytest.raises(DomainValidationError):  # same plan
        sub.change_plan(PlanCode.PRO)


def test_cancel_and_reactivate() -> None:
    sub, _ = Subscription.start(organization_id=uuid4())
    event = sub.cancel()
    assert isinstance(event, SubscriptionCancelled) and sub.status.value == "cancelled"
    with pytest.raises(SubscriptionStateError):  # cannot change plan while cancelled
        sub.change_plan(PlanCode.PRO)
    sub.reactivate()
    assert sub.status.value == "active" and sub.cancelled_at is None
    with pytest.raises(SubscriptionStateError):
        sub.reactivate()


def test_past_due_flow() -> None:
    sub, _ = Subscription.start(organization_id=uuid4())
    sub.mark_past_due()
    assert sub.status.value == "past_due"
    sub.reactivate()
    assert sub.status.value == "active"
