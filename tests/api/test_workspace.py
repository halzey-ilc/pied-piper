"""Workspace layout API: scopes by role, validation, effective layout, isolation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)

_ORDER = ["focus_deals", "work_session", "counters", "plans", "month_metrics"]


def _in_department(user: Any, tenant: TenantFixture, name: str) -> str:
    from modules.organizations.infrastructure.models import Department, Membership

    department, _ = Department.objects.get_or_create(organization=tenant.org, name=name)
    Membership.objects.filter(user=user, organization=tenant.org).update(department=department)
    return str(department.id)


def _blocks(order: list[str], hidden: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"blocks": [{"id": b, "visible": b not in hidden} for b in order]}


def test_default_layout_for_everyone(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    manager = make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP)
    body = client_for(manager, "acme").get("/api/v1/workspace/layout/effective/").json()
    assert [b["id"] for b in body["blocks"]] == [
        "work_session",
        "counters",
        "plans",
        "month_metrics",
        "focus_deals",
    ]
    assert all(b["visible"] for b in body["blocks"]) and body["is_default"] is True


def test_director_company_layout_and_department_override(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    director = make_user("boss", tenant=tenants["acme"], role=Role.DIRECTOR)
    rep = make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP)
    north = _in_department(rep, tenants["acme"], "North")
    boss = client_for(director, "acme")

    saved = boss.put("/api/v1/workspace/layout/", _blocks(_ORDER, hidden=("plans",)), format="json")
    assert saved.status_code == 200, saved.content
    effective = client_for(rep, "acme").get("/api/v1/workspace/layout/effective/").json()
    assert [b["id"] for b in effective["blocks"]] == _ORDER
    assert {b["id"]: b["visible"] for b in effective["blocks"]}["plans"] is False

    # The department's own layout wins over the company's.
    dept = boss.put(
        f"/api/v1/workspace/layout/?department_id={north}",
        _blocks(["counters"], hidden=("counters",)),
        format="json",
    )
    assert dept.status_code == 200
    assert dept.json()["blocks"][0]["id"] == "counters"  # the missing ones are appended
    effective = client_for(rep, "acme").get("/api/v1/workspace/layout/effective/").json()
    assert effective["department_id"] == north
    assert effective["blocks"][0] == {"id": "counters", "visible": False}


def test_head_of_sales_only_own_department_manager_cannot_configure(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    rop = make_user("rop", tenant=tenants["acme"], role=Role.HEAD_OF_SALES)
    own = _in_department(rop, tenants["acme"], "North")
    other_user = make_user("rop2", tenant=tenants["acme"], role=Role.HEAD_OF_SALES)
    other = _in_department(other_user, tenants["acme"], "South")
    client = client_for(rop, "acme")
    assert (
        client.put(
            f"/api/v1/workspace/layout/?department_id={own}", _blocks(_ORDER), format="json"
        ).status_code
        == 200
    )
    assert (
        client.put(
            f"/api/v1/workspace/layout/?department_id={other}", _blocks(_ORDER), format="json"
        ).status_code
        == 403
    )
    assert (
        client.put("/api/v1/workspace/layout/", _blocks(_ORDER), format="json").status_code == 403
    )

    rep = make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP)
    assert (
        client_for(rep, "acme")
        .put("/api/v1/workspace/layout/", _blocks(_ORDER), format="json")
        .status_code
        == 403
    )
    assert client_for(rep, "acme").get("/api/v1/workspace/layout/").status_code == 403


def test_validation_unknown_and_duplicate_blocks(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("boss", tenant=tenants["acme"], role=Role.DIRECTOR), "acme")
    unknown = {"blocks": [{"id": "secret_widget", "visible": True}]}
    assert client.put("/api/v1/workspace/layout/", unknown, format="json").status_code == 400
    twice = _blocks(["plans", "plans"])
    assert client.put("/api/v1/workspace/layout/", twice, format="json").status_code == 400
    missing_department = "00000000-0000-0000-0000-000000000001"
    assert (
        client.put(
            f"/api/v1/workspace/layout/?department_id={missing_department}",
            _blocks(_ORDER),
            format="json",
        ).status_code
        == 404
    )


def test_tenant_isolation(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    acme = client_for(make_user("boss", tenant=tenants["acme"], role=Role.DIRECTOR), "acme")
    assert (
        acme.put(
            "/api/v1/workspace/layout/", _blocks(_ORDER, hidden=("plans",)), format="json"
        ).status_code
        == 200
    )
    globex_rep = make_user("g-rep", tenant=tenants["globex"], role=Role.SALES_REP)
    body = client_for(globex_rep, "globex").get("/api/v1/workspace/layout/effective/").json()
    assert body["is_default"] is True
