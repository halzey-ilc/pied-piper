"""Planning API: sales plans with history, absences, RBAC, period validation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)


def _in_department(tenant: TenantFixture, name: str, *users: Any) -> str:
    from modules.organizations.infrastructure.models import Department, Membership

    department, _ = Department.objects.get_or_create(organization=tenant.org, name=name)
    Membership.objects.filter(user__in=users, organization=tenant.org).update(department=department)
    return str(department.id)


def _set_plan(client: APIClient, **body: Any) -> Any:
    body.setdefault("scope", "company")
    body.setdefault("metric", "monthly_sales")
    body.setdefault("period", "2026-11")
    body.setdefault("target", "20")
    return client.put("/api/v1/plans/", body, format="json")


def test_set_update_plan_and_history(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")

    created = _set_plan(client, target="20")
    assert created.status_code == 200, created.content
    plan_id = created.json()["id"]
    assert created.json()["target"] == "20.00"

    # Setting the same (scope, metric, period) updates the existing plan.
    updated = _set_plan(client, target="25")
    assert updated.json()["id"] == plan_id and updated.json()["target"] == "25.00"
    assert client.get("/api/v1/plans/").json()["count"] == 1

    history = client.get(f"/api/v1/plans/{plan_id}/history/").json()
    assert history["count"] == 2
    assert history["results"][0]["old_target"] is None
    assert history["results"][1]["old_target"] == "20.00"
    assert history["results"][1]["new_target"] == "25.00"


def test_plan_rbac_and_period_validation(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    head_user = make_user("head", tenant=tenants["acme"], role=Role.MANAGER)  # head of sales
    head = client_for(head_user, "acme")
    rep_user = make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP)
    rep = client_for(rep_user, "acme")
    _in_department(tenants["acme"], "Sales", head_user, rep_user)

    # A head of sales cannot set a company plan, but can set a manager's plan.
    assert _set_plan(head, scope="company", target="30").status_code == 403
    user_plan = _set_plan(
        head,
        scope="user",
        scope_id=str(rep_user.id),
        metric="daily_calls",
        period="2026-11-05",
        target="40",
    )
    assert user_plan.status_code == 200, user_plan.content

    # A sales rep cannot set plans at all.
    assert _set_plan(rep, scope="user", scope_id=str(rep_user.id)).status_code == 403

    # Period must match the metric granularity.
    bad = _set_plan(owner, metric="monthly_sales", period="2026-13", target="10")
    assert bad.status_code == 400


def test_absence_create_list_and_rbac(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    rep_user = make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP)
    rep = client_for(rep_user, "acme")

    created = owner.post(
        "/api/v1/absences/",
        {
            "user_id": str(rep_user.id),
            "kind": "vacation",
            "start_date": "2026-11-10",
            "end_date": "2026-11-20",
            "comment": "annual leave",
        },
        format="json",
    )
    assert created.status_code == 201, created.content

    listed = owner.get("/api/v1/absences/").json()
    assert listed["count"] == 1 and listed["results"][0]["kind"] == "vacation"
    # Overlap filter: a window before the absence returns nothing.
    assert (
        owner.get("/api/v1/absences/?active_from=2026-10-01&active_to=2026-10-31").json()["count"]
        == 0
    )

    # end before start is rejected.
    assert (
        owner.post(
            "/api/v1/absences/",
            {
                "user_id": str(rep_user.id),
                "kind": "sick",
                "start_date": "2026-11-20",
                "end_date": "2026-11-10",
            },
            format="json",
        ).status_code
        == 400
    )

    # A sales rep cannot manage absences.
    assert (
        rep.post(
            "/api/v1/absences/",
            {
                "user_id": str(rep_user.id),
                "kind": "day_off",
                "start_date": "2026-11-10",
                "end_date": "2026-11-10",
            },
            format="json",
        ).status_code
        == 403
    )


def test_money_plan_currency_and_actual_in_that_currency(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    # Count plans carry no currency; money plans default to the company currency.
    assert _set_plan(client, target="10").json()["currency"] is None
    default = _set_plan(client, metric="monthly_turnover", target="1000")
    assert default.status_code == 200, default.content
    assert len(default.json()["currency"]) == 3

    usd = _set_plan(client, metric="monthly_turnover", target="1000", currency="usd")
    assert usd.json()["currency"] == "USD" and usd.json()["id"] == default.json()["id"]
    assert (
        _set_plan(client, metric="monthly_turnover", target="1", currency="US").status_code == 400
    )

    # The actual counts only payments in the plan's currency.
    customer = client.post("/api/v1/customers/", {"name": "Acme"}, format="json").json()["id"]
    for amount, currency, day in (("300", "USD", "10"), ("500", "KGS", "11")):
        paid = client.post(
            "/api/v1/payments/",
            {
                "amount": amount,
                "currency": currency,
                "kind": "full",
                "paid_on": f"2026-11-{day}",
                "customer_id": customer,
            },
            format="json",
        )
        assert paid.status_code == 201, paid.content
    progress = client.get(
        "/api/v1/plans/progress/?metric=monthly_turnover&period=2026-11&scope=company"
    ).json()["results"][0]
    assert progress["currency"] == "USD"
    assert progress["actual"] in ("300", "300.00")


def test_distribute_company_plan_between_departments(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    head_user = make_user("head", tenant=tenants["acme"], role=Role.MANAGER)
    head = client_for(head_user, "acme")
    north = _in_department(tenants["acme"], "North", head_user)
    south = _in_department(tenants["acme"], "South")
    body = {
        "metric": "monthly_sales",
        "period": "2026-11",
        "parts": [{"department_id": north, "target": "6"}, {"department_id": south, "target": "4"}],
    }
    # No company plan yet.
    assert owner.post("/api/v1/plans/distribute/", body, format="json").status_code == 400
    assert _set_plan(owner, target="10").status_code == 200

    wrong = dict(body, parts=[{"department_id": north, "target": "6"}])
    assert owner.post("/api/v1/plans/distribute/", wrong, format="json").status_code == 400
    assert head.post("/api/v1/plans/distribute/", body, format="json").status_code == 403
    unknown = dict(
        body,
        parts=[
            {"department_id": north, "target": "6"},
            {"department_id": "00000000-0000-0000-0000-000000000001", "target": "4"},
        ],
    )
    assert owner.post("/api/v1/plans/distribute/", unknown, format="json").status_code == 404

    done = owner.post("/api/v1/plans/distribute/", body, format="json")
    assert done.status_code == 200, done.content
    assert {p["scope_id"]: p["target"] for p in done.json()["results"]} == {
        north: "6.00",
        south: "4.00",
    }
    # Redistribution updates the same plans and keeps their history.
    again = dict(
        body,
        parts=[{"department_id": north, "target": "7"}, {"department_id": south, "target": "3"}],
    )
    assert owner.post("/api/v1/plans/distribute/", again, format="json").status_code == 200
    plans = owner.get("/api/v1/plans/?scope=department").json()
    assert plans["count"] == 2
    north_plan = next(p for p in plans["results"] if p["scope_id"] == north)
    assert owner.get(f"/api/v1/plans/{north_plan['id']}/history/").json()["count"] == 2
    # A department plan for a department that does not exist is refused.
    assert (
        _set_plan(
            owner, scope="department", scope_id="00000000-0000-0000-0000-000000000002"
        ).status_code
        == 404
    )
