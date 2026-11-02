"""Access model (spec §3, agreed 01.11.2026): director — the company; head of sales — their
department; manager and viewer — names of colleagues, nothing confidential; companies isolated."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.organizations.infrastructure.models import Department, Membership
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)


def _in_department(tenant: TenantFixture, name: str, *users: Any) -> str:
    department, _ = Department.objects.get_or_create(organization=tenant.org, name=name)
    Membership.objects.filter(user__in=users, organization=tenant.org).update(department=department)
    return str(department.id)


def _plan(client: APIClient, **body: Any) -> Any:
    body.setdefault("metric", "monthly_sales")
    body.setdefault("period", "2026-11")
    body.setdefault("target", "10")
    return client.put("/api/v1/plans/", body, format="json")


@pytest.fixture
def company(make_user: Callable[..., Any], tenants: dict[str, TenantFixture]) -> dict[str, Any]:
    """Director, departments North and South (a head of sales and a manager each), a viewer."""
    acme = tenants["acme"]
    people = {
        "director": make_user("director", tenant=acme, role=Role.DIRECTOR),
        "rop_north": make_user("rop_north", tenant=acme, role=Role.HEAD_OF_SALES),
        "rep_north": make_user("rep_north", tenant=acme, role=Role.SALES_REP),
        "rop_south": make_user("rop_south", tenant=acme, role=Role.HEAD_OF_SALES),
        "rep_south": make_user("rep_south", tenant=acme, role=Role.SALES_REP),
        "viewer": make_user("viewer", tenant=acme, role=Role.VIEWER),
    }
    for name, user in people.items():
        user.first_name = name.replace("_", " ").title()
        user.save()
    north = _in_department(acme, "North", people["rop_north"], people["rep_north"])
    south = _in_department(acme, "South", people["rop_south"], people["rep_south"])
    return {**people, "north": north, "south": south}


def test_every_member_sees_colleague_names_and_nothing_more(
    company: dict[str, Any], client_for: Callable[..., APIClient]
) -> None:
    for role in ("rep_north", "viewer", "rop_north", "director"):
        response = client_for(company[role], "acme").get("/api/v1/staff/names/")
        assert response.status_code == 200, (role, response.content)
        rows = response.json()["results"]
        # Names of the whole company, other departments included...
        assert {r["full_name"] for r in rows} >= {"Rep South", "Rop South", "Director"}
        # ...and only names: no contacts, roles, departments or activity.
        assert all(set(r) == {"user_id", "full_name"} for r in rows)

    # The staff list with contacts stays closed to managers and viewers.
    for role in ("rep_north", "viewer"):
        assert client_for(company[role], "acme").get("/api/v1/staff/").status_code == 403


def test_names_exclude_dismissed_staff_and_other_companies(
    company: dict[str, Any],
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    outsider = make_user("outsider", tenant=tenants["globex"], role=Role.SALES_REP)
    Membership.objects.filter(user=company["rep_south"]).update(is_active=False)

    names = {
        r["user_id"]
        for r in client_for(company["rep_north"], "acme")
        .get("/api/v1/staff/names/")
        .json()["results"]
    }
    assert str(company["rep_south"].id) not in names
    assert str(outsider.id) not in names
    # Another company's directory is not reachable at all.
    assert client_for(company["rep_north"], "globex").get("/api/v1/staff/names/").status_code == 404


def test_closed_cabinets_and_colleague_plans_stay_closed_to_managers_and_viewers(
    company: dict[str, Any], client_for: Callable[..., APIClient]
) -> None:
    director = client_for(company["director"], "acme")
    north_plan = _plan(
        director,
        scope="user",
        scope_id=str(company["rep_north"].id),
        metric="daily_calls",
        period="2026-11-05",
    ).json()
    south_plan = _plan(
        director,
        scope="user",
        scope_id=str(company["rep_south"].id),
        metric="daily_calls",
        period="2026-11-05",
    ).json()

    rep = client_for(company["rep_north"], "acme")
    assert {p["id"] for p in rep.get("/api/v1/plans/").json()["results"]} == {north_plan["id"]}
    assert rep.get(f"/api/v1/plans/{south_plan['id']}/history/").status_code == 404
    assert rep.get(f"/api/v1/plans/{north_plan['id']}/history/").status_code == 200

    for role in ("rep_north", "viewer"):
        assert client_for(company[role], "acme").get("/api/v1/team/today/").status_code == 403
    viewer = client_for(company["viewer"], "acme")
    assert viewer.get("/api/v1/plans/").status_code == 403
    assert viewer.get(f"/api/v1/plans/{south_plan['id']}/history/").status_code == 403


def test_head_of_sales_manages_plans_and_absences_of_their_department_only(
    company: dict[str, Any], client_for: Callable[..., APIClient]
) -> None:
    director = client_for(company["director"], "acme")
    company_plan = _plan(director, scope="company").json()
    north_plan = _plan(director, scope="department", scope_id=company["north"]).json()
    south_plan = _plan(director, scope="department", scope_id=company["south"]).json()
    south_rep_plan = _plan(
        director,
        scope="user",
        scope_id=str(company["rep_south"].id),
        metric="daily_calls",
        period="2026-11-05",
    ).json()

    rop = client_for(company["rop_north"], "acme")
    own_rep_plan = _plan(
        rop,
        scope="user",
        scope_id=str(company["rep_north"].id),
        metric="daily_calls",
        period="2026-11-05",
    )
    assert own_rep_plan.status_code == 200, own_rep_plan.content
    assert (
        _plan(
            rop,
            scope="user",
            scope_id=str(company["rep_south"].id),
            metric="daily_calls",
            period="2026-11-06",
        ).status_code
        == 403
    )
    # Company and department targets stay the director's.
    assert _plan(rop, scope="department", scope_id=company["north"]).status_code == 403

    visible = {p["id"] for p in rop.get("/api/v1/plans/").json()["results"]}
    assert {company_plan["id"], north_plan["id"], own_rep_plan.json()["id"]} <= visible
    assert south_plan["id"] not in visible and south_rep_plan["id"] not in visible
    assert rop.get(f"/api/v1/plans/{south_rep_plan['id']}/history/").status_code == 404

    absence = {"kind": "vacation", "start_date": "2026-11-10", "end_date": "2026-11-12"}
    own = rop.post(
        "/api/v1/absences/", {**absence, "user_id": str(company["rep_north"].id)}, format="json"
    )
    assert own.status_code == 201, own.content
    foreign = rop.post(
        "/api/v1/absences/", {**absence, "user_id": str(company["rep_south"].id)}, format="json"
    )
    assert foreign.status_code == 403
    director.post(
        "/api/v1/absences/", {**absence, "user_id": str(company["rep_south"].id)}, format="json"
    )
    listed = {a["user_id"] for a in rop.get("/api/v1/absences/").json()["results"]}
    assert listed == {str(company["rep_north"].id)}
    assert len(director.get("/api/v1/absences/").json()["results"]) == 2


def test_head_of_sales_cabinet_and_staff_are_their_department(
    company: dict[str, Any], client_for: Callable[..., APIClient]
) -> None:
    rop = client_for(company["rop_north"], "acme")
    staff = {m["user_id"] for m in rop.get("/api/v1/staff/").json()["results"]}
    assert str(company["rep_north"].id) in staff and str(company["rep_south"].id) not in staff

    # Asking for another department's board returns the caller's own department.
    board = rop.get(f"/api/v1/team/today/?department_id={company['south']}")
    assert board.status_code == 200, board.content
    assert str(company["rep_south"].id) not in board.content.decode()


def test_heads_of_sales_hire_and_dismiss_managers_of_their_department_only(
    company: dict[str, Any], client_for: Callable[..., APIClient]
) -> None:
    rop = client_for(company["rop_north"], "acme")
    director = client_for(company["director"], "acme")
    for role in ("director", "head_of_sales", "viewer"):
        assert (
            rop.post("/api/v1/staff/", {"first_name": "X", "role": role}, format="json").status_code
            == 403
        )
    hired = rop.post(
        "/api/v1/staff/",
        {"first_name": "New", "role": "sales_rep", "department_id": company["south"]},
        format="json",
    )
    assert hired.status_code == 201, hired.content
    assert hired.json()["member"]["department"]["id"] == company["north"]

    # Their own manager: yes. Another department's manager, a peer, the director: no.
    north_rep = Membership.objects.get(user=company["rep_north"])
    for outsider in ("rep_south", "rop_south", "director"):
        membership = Membership.objects.get(user=company[outsider])
        assert rop.post(f"/api/v1/staff/{membership.id}/deactivate/").status_code == 404
        assert Membership.objects.get(id=membership.id).is_active
    assert rop.post(f"/api/v1/staff/{north_rep.id}/deactivate/").status_code == 200

    south_rep = Membership.objects.get(user=company["rep_south"])
    assert director.post(f"/api/v1/staff/{south_rep.id}/deactivate/").status_code == 200
