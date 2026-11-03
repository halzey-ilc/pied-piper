"""Plan limits (usage, warnings, enforcement, platform overrides) and the audit journal."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.audit.infrastructure.models import AuditRecord
from modules.organizations.infrastructure.models import Department, Membership
from modules.subscriptions.application.usage import plan_usage
from modules.subscriptions.infrastructure.models import SubscriptionRecord
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)


def _subscribe(tenant: TenantFixture, **overrides: int) -> None:
    SubscriptionRecord.objects.create(
        id=uuid.uuid4(),
        organization=tenant.org,
        plan_code="free",
        started_at=datetime.now(UTC),
        limit_overrides=overrides or None,
    )


def test_usage_warns_and_creation_over_the_limit_is_refused(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    _subscribe(tenants["acme"], customers=2)
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    assert client.post("/api/v1/customers/", {"name": "A"}, format="json").status_code == 201

    usage = client.get("/api/v1/subscription/usage/").json()
    customers = next(m for m in usage["metrics"] if m["metric"] == "customers")
    assert customers == {
        "metric": "customers",
        "used": 1,
        "limit": 2,
        "state": "ok",
        "overridden": True,
    }
    deals = next(m for m in usage["metrics"] if m["metric"] == "deals")
    assert deals["limit"] == 200 and deals["overridden"] is False  # the free plan's own

    assert client.post("/api/v1/customers/", {"name": "B"}, format="json").status_code == 201
    reached = client.get("/api/v1/subscription/usage/").json()
    assert next(m for m in reached["metrics"] if m["metric"] == "customers")["state"] == "reached"

    refused = client.post("/api/v1/customers/", {"name": "C"}, format="json")
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "plan_limit_reached"
    # Reading and editing are not limited.
    assert client.get("/api/v1/customers/").status_code == 200


def test_unlimited_override_lifts_the_plan_limit(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    _subscribe(tenants["acme"], customers=-1)
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    usage = client.get("/api/v1/subscription/usage/").json()
    assert next(m for m in usage["metrics"] if m["metric"] == "customers")["limit"] == -1
    assert client.post("/api/v1/customers/", {"name": "A"}, format="json").status_code == 201


def test_platform_admin_overrides_limits_with_audit(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    _subscribe(tenants["acme"])
    admin_user = make_user("platform")
    admin_user.is_platform_admin = True
    admin_user.save()
    admin = client_for(admin_user, None)
    company = tenants["acme"].org.id
    url = f"/api/v1/platform/companies/{company}/limits/"

    before = admin.get(url).json()
    assert {m["metric"]: m["limit"] for m in before["metrics"]} == {
        "users": 3,
        "customers": 100,
        "deals": 200,
    }
    changed = admin.put(url, {"limits": {"users": 10, "deals": -1}}, format="json")
    assert changed.status_code == 200, changed.content
    limits = {m["metric"]: (m["limit"], m["overridden"]) for m in changed.json()["metrics"]}
    assert limits == {"users": (10, True), "customers": (100, False), "deals": (-1, True)}
    assert AuditRecord.objects.using("default").filter(action="platform.limits_changed").exists()

    assert admin.put(url, {"limits": {"calls": 5}}, format="json").status_code == 400
    assert admin.put(url, {"limits": {"users": -2}}, format="json").status_code == 400
    reset = admin.put(url, {"limits": {}}, format="json").json()
    assert all(not m["overridden"] for m in reset["metrics"])

    owner = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    assert owner.put(url, {"limits": {"users": 99}}, format="json").status_code == 403


def test_audit_journal_scope_by_role(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    acme = tenants["acme"]
    director = make_user("director", tenant=acme, role=Role.DIRECTOR)
    rop = make_user("rop", tenant=acme, role=Role.HEAD_OF_SALES)
    rep = make_user("rep", tenant=acme, role=Role.SALES_REP)
    outsider = make_user("outsider", tenant=acme, role=Role.SALES_REP)
    north = Department.objects.create(organization=acme.org, name="North")
    Membership.objects.filter(user__in=[rop, rep]).update(department=north)

    rep_client = client_for(rep, "acme")
    outsider_client = client_for(outsider, "acme")
    customer = rep_client.post("/api/v1/customers/", {"name": "Rep's"}, format="json").json()
    outsider_client.post("/api/v1/customers/", {"name": "Other"}, format="json")

    everything = client_for(director, "acme").get("/api/v1/audit/").json()
    actors = {row["actor"]["id"] for row in everything["results"] if row["actor"]}
    assert {str(rep.id), str(outsider.id)} <= actors
    assert everything["results"][0]["actor"]["name"] in {"rep", "outsider"}

    department = client_for(rop, "acme").get("/api/v1/audit/").json()
    assert {row["actor"]["id"] for row in department["results"]} == {str(rep.id)}

    one = client_for(director, "acme").get(
        f"/api/v1/audit/?resource_type=customer&resource_id={customer['id']}&action=customer."
    )
    assert one.status_code == 200
    assert [row["resource_id"] for row in one.json()["results"]] == [customer["id"]]

    assert rep_client.get("/api/v1/audit/").status_code == 403
    assert client_for(director, "acme").get("/api/v1/audit/?date_from=nope").status_code == 400
    # Another company's journal is not reachable through this one.
    assert client_for(director, "globex").get("/api/v1/audit/").status_code == 404


def test_usage_without_subscription_is_uncapped() -> None:
    # The access gate lets a company without a subscription create anything,
    # so its usage must not warn about plan limits either.
    usage = plan_usage(None, {"users": 50, "customers": 1000, "deals": 5000})
    assert {m.metric: (m.limit, m.state) for m in usage.metrics} == {
        "users": (-1, "ok"),
        "customers": (-1, "ok"),
        "deals": (-1, "ok"),
    }
