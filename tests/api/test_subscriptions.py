"""Subscription API (control plane): current, change plan, cancel, RBAC, isolation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.subscriptions.infrastructure.models import SubscriptionRecord
from platform_core.infrastructure.messaging.models import OutboxMessage
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)


def test_current_creates_free_then_change_plan(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    current = client.get("/api/v1/subscription/")
    assert current.status_code == 200
    assert current.json()["plan"]["code"] == "free"
    assert current.json()["status"] == "active"
    # Created in the control-plane database.
    assert (
        SubscriptionRecord.objects.using("default")
        .filter(organization=tenants["acme"].org)
        .exists()
    )

    changed = client.post("/api/v1/subscription/", {"plan_code": "pro"}, format="json")
    assert changed.status_code == 200 and changed.json()["plan"]["code"] == "pro"
    assert changed.json()["plan"]["limits"]["users"] == 25
    assert (
        OutboxMessage.objects.using("default")
        .filter(event_type="subscriptions.plan_changed")
        .exists()
    )


def test_cancel_and_reactivate(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    client.get("/api/v1/subscription/")
    cancelled = client.post("/api/v1/subscription/cancel/", {}, format="json")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    reactivated = client.post("/api/v1/subscription/reactivate/", {}, format="json")
    assert reactivated.json()["status"] == "active"


def test_plans_listing(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    plans = client.get("/api/v1/subscription/plans/").json()["plans"]
    assert {p["code"] for p in plans} == {"free", "pro", "enterprise"}


def test_rbac(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    manager = client_for(make_user("mgr", tenant=tenants["acme"], role=Role.MANAGER), "acme")
    rep = client_for(make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP), "acme")
    # Managers can view but not manage.
    assert manager.get("/api/v1/subscription/").status_code == 200
    assert (
        manager.post("/api/v1/subscription/", {"plan_code": "pro"}, format="json").status_code
        == 403
    )
    # Sales reps cannot even view billing.
    assert rep.get("/api/v1/subscription/").status_code == 403


def test_isolation_between_organizations(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    acme_owner = client_for(make_user("a-owner", tenant=tenants["acme"]), "acme")
    globex_owner = client_for(make_user("g-owner", tenant=tenants["globex"]), "globex")
    acme_owner.post("/api/v1/subscription/", {"plan_code": "enterprise"}, format="json")
    globex_owner.get("/api/v1/subscription/")  # Free

    assert acme_owner.get("/api/v1/subscription/").json()["plan"]["code"] == "enterprise"
    assert globex_owner.get("/api/v1/subscription/").json()["plan"]["code"] == "free"
    # A user not in an org cannot read its subscription.
    outsider = make_user("outsider", tenant=tenants["acme"])
    assert client_for(outsider, "globex").get("/api/v1/subscription/").status_code == 404
