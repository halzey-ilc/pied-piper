"""Planning API: sales plans with history, absences, RBAC, period validation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)


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
