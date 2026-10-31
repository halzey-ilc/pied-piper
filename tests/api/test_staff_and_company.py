"""P0-1: company settings, departments, staff accounts, roles and team visibility."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.audit.infrastructure.models import AuditRecord
from modules.organizations.infrastructure.models import Membership
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)


def _ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.content
    return response.json()


def _department(director: APIClient, name: str) -> str:
    dept_id: str = _ok(director.post("/api/v1/departments/", {"name": name}, format="json"), 201)[
        "id"
    ]
    return dept_id


def _create(client: APIClient, **body: Any) -> dict[str, Any]:
    result: dict[str, Any] = _ok(client.post("/api/v1/staff/", body, format="json"), 201)
    return result


def _user(member: dict[str, Any]) -> Any:
    return get_user_model().objects.get(id=member["member"]["user_id"])


def test_director_builds_the_company(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    director = client_for(owner, "acme")

    company = _ok(director.get("/api/v1/company/"))
    assert (company["country"], company["currency"], company["timezone"]) == (
        "KG",
        "KGS",
        "Asia/Bishkek",
    )
    updated = _ok(director.patch("/api/v1/company/", {"timezone": "Asia/Almaty"}, format="json"))
    assert updated["timezone"] == "Asia/Almaty"
    assert (
        director.patch("/api/v1/company/", {"timezone": "Mars/Base"}, format="json").status_code
        == 400
    )
    assert director.patch("/api/v1/company/", {"currency": "som"}, format="json").status_code == 400

    sales = _department(director, "Продажи")
    assert (
        director.post("/api/v1/departments/", {"name": "продажи"}, format="json").status_code == 409
    )

    created = _create(
        director, first_name="Айгерим", last_name="Ким", role="head_of_sales", department_id=sales
    )
    member = created["member"]
    assert member["product_role"] == "head_of_sales" and member["department"]["id"] == sales
    assert member["status"] == "invited" and len(created["temporary_password"]) >= 12
    user = _user(created)
    assert user.must_change_password and user.check_password(created["temporary_password"])
    # The password is never returned again.
    listing = _ok(director.get("/api/v1/staff/"))
    assert "temporary_password" not in str(listing)
    assert {"staff.created", "department.created", "company.updated"} <= set(
        AuditRecord.objects.using("tenant_acme").values_list("action", flat=True)
    )


def test_temporary_password_must_be_changed_first(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    director = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    created = _create(director, first_name="Bakyt", role="sales_rep")
    user = _user(created)
    client = client_for(user, "acme")
    blocked = client.get("/api/v1/customers/")
    assert blocked.status_code == 403
    assert _ok(client_for(user, None).get("/api/v1/me/"))["must_change_password"] is True

    change = client_for(user, None).post(
        "/api/v1/auth/password/change/",
        {"current_password": created["temporary_password"], "new_password": "Correct-Horse-42!"},
        format="json",
    )
    assert change.status_code == 204, change.content
    assert client.get("/api/v1/customers/").status_code == 200
    wrong = client_for(user, None).post(
        "/api/v1/auth/password/change/",
        {"current_password": "nope", "new_password": "Another-Pass-77!"},
        format="json",
    )
    assert wrong.status_code == 400


def test_head_of_sales_manages_only_their_department(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    director = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    sales, other = _department(director, "Sales"), _department(director, "Other")
    hos = _user(_create(director, first_name="Head", role="head_of_sales", department_id=sales))
    hos.must_change_password = False
    hos.save()
    hos_client = client_for(hos, "acme")

    # A head of sales creates in their own department, whatever department was sent.
    rep = _create(hos_client, first_name="Rep", role="sales_rep", department_id=other)
    assert rep["member"]["department"]["id"] == sales
    assert (
        hos_client.post(
            "/api/v1/staff/", {"first_name": "X", "role": "director"}, format="json"
        ).status_code
        == 403
    )
    assert (
        hos_client.post(
            "/api/v1/staff/", {"first_name": "X", "role": "head_of_sales"}, format="json"
        ).status_code
        == 403
    )

    outsider = _create(director, first_name="Out", role="sales_rep", department_id=other)
    visible = {m["id"] for m in _ok(hos_client.get("/api/v1/staff/"))["results"]}
    assert rep["member"]["id"] in visible and outsider["member"]["id"] not in visible
    assert (
        hos_client.post(f"/api/v1/staff/{outsider['member']['id']}/deactivate/").status_code == 404
    )
    # Moving between departments is the director's call.
    move = hos_client.patch(
        f"/api/v1/staff/{rep['member']['id']}/", {"department_id": other}, format="json"
    )
    assert move.status_code == 403

    reset = _ok(hos_client.post(f"/api/v1/staff/{rep['member']['id']}/reset-password/"))
    assert _user(rep).check_password(reset["temporary_password"])


def test_deactivation_blocks_access_and_keeps_history(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    director = client_for(owner, "acme")
    created = _create(director, first_name="Leaving", role="sales_rep")
    user = _user(created)
    user.must_change_password = False
    user.save()
    member_id = created["member"]["id"]
    assert client_for(user, "acme").get("/api/v1/customers/").status_code == 200

    gone = _ok(director.post(f"/api/v1/staff/{member_id}/deactivate/"))
    assert gone["status"] == "deactivated"
    user.refresh_from_db()
    assert not user.is_active
    assert Membership.objects.filter(id=member_id).exists()  # never deleted
    assert director.post(
        f"/api/v1/staff/{_ok(director.get('/api/v1/staff/'))['results'][0]['id']}/deactivate/"
    ).status_code in (200, 400)
    own = Membership.objects.get(user=owner)
    assert director.post(f"/api/v1/staff/{own.id}/deactivate/").status_code == 400  # not yourself


def test_roles_without_staff_rights(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    for role in (Role.SALES_REP, Role.LEAD_CALLER, Role.OPERATIONS, Role.VIEWER):
        client = client_for(make_user(role.value, tenant=tenants["acme"], role=role), "acme")
        assert client.get("/api/v1/staff/").status_code == 403
        assert (
            client.post(
                "/api/v1/staff/", {"first_name": "X", "role": "sales_rep"}, format="json"
            ).status_code
            == 403
        )
        assert client.patch("/api/v1/company/", {"name": "X"}, format="json").status_code == 403
        assert client.get("/api/v1/company/").status_code == 200


def test_one_active_company_per_person(
    make_user: Callable[..., Any], tenants: dict[str, TenantFixture]
) -> None:
    user = make_user("someone", tenant=tenants["acme"])
    with pytest.raises(IntegrityError), transaction.atomic():
        Membership.objects.create(user=user, organization=tenants["globex"].org, role="owner")
    # After leaving the first company the person may be added to another one.
    Membership.objects.filter(user=user).update(is_active=False)
    Membership.objects.create(user=user, organization=tenants["globex"].org, role="owner")


def test_me_lists_product_role_and_permissions(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    rep = make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP)
    me = _ok(client_for(rep, None).get("/api/v1/me/"))
    [membership] = me["memberships"]
    assert membership["product_role"] == "manager"
    assert "calls.ratings.view" not in membership["permissions"]
    assert "deals.view" in membership["permissions"]
    assert me["is_platform_admin"] is False


def _pipeline(client: APIClient) -> None:
    stages = [{"name": "New", "probability": 10}, {"name": "Won", "probability": 100}]
    _ok(
        client.post(
            "/api/v1/sales/pipelines/",
            {"name": "P", "is_default": True, "stages": stages},
            format="json",
        ),
        201,
    )


def test_team_visibility_follows_departments(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    director = client_for(owner, "acme")
    sales, other = _department(director, "Sales"), _department(director, "Other")

    def staff(name: str, role: str, dept: str) -> APIClient:
        user = _user(_create(director, first_name=name, role=role, department_id=dept))
        user.must_change_password = False
        user.save()
        return client_for(user, "acme")

    hos = staff("Head", "head_of_sales", sales)
    rep = staff("Rep", "sales_rep", sales)
    stranger = staff("Stranger", "sales_rep", other)
    caller = staff("Caller", "lead_caller", sales)
    ops = staff("Ops", "operations", other)
    _pipeline(director)

    deal = _ok(
        rep.post("/api/v1/sales/deals/", {"title": "D", "amount": "10.00"}, format="json"), 201
    )
    assert deal["currency"] == "KGS"  # the company currency, not USD
    far = _ok(
        stranger.post("/api/v1/sales/deals/", {"title": "F", "amount": "5.00"}, format="json"), 201
    )

    hos_deals = {d["id"] for d in _ok(hos.get("/api/v1/sales/deals/"))["results"]}
    assert deal["id"] in hos_deals and far["id"] not in hos_deals  # TEAM = own department
    assert hos.get(f"/api/v1/sales/deals/{far['id']}/").status_code == 404
    rep_deals = {d["id"] for d in _ok(rep.get("/api/v1/sales/deals/"))["results"]}
    assert rep_deals == {deal["id"]}  # a manager sees only their own
    assert caller.get("/api/v1/sales/deals/").status_code == 403  # лидоруб: no deals
    ops_deals = {d["id"] for d in _ok(ops.get("/api/v1/sales/deals/"))["results"]}
    assert {deal["id"], far["id"]} <= ops_deals  # опердел: support after the sale, company-wide

    lead = _ok(
        caller.post(
            "/api/v1/leads/",
            {"name": "L", "source": "website", "phone": "+996555000001"},
            format="json",
        ),
        201,
    )
    assert lead["id"] in {x["id"] for x in _ok(hos.get("/api/v1/leads/"))["results"]}


def test_company_isolation(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    acme = client_for(make_user("acme-owner", tenant=tenants["acme"]), "acme")
    globex = client_for(make_user("globex-owner", tenant=tenants["globex"]), "globex")
    dept = _department(acme, "Sales")
    member = _create(acme, first_name="A", role="sales_rep")["member"]["id"]
    # Known ids of another company behave like missing ones.
    assert (
        globex.patch(f"/api/v1/departments/{dept}/", {"name": "x"}, format="json").status_code
        == 404
    )
    assert globex.post(f"/api/v1/staff/{member}/deactivate/").status_code == 404
    assert (
        globex.post(
            "/api/v1/staff/",
            {"first_name": "B", "role": "sales_rep", "department_id": dept},
            format="json",
        ).status_code
        == 400
    )
    assert member not in str(_ok(globex.get("/api/v1/staff/")))


def test_backfill_gives_old_records_their_department(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    from django.core.management import call_command

    from modules.organizations.infrastructure.models import Department
    from modules.sales_pipeline.infrastructure.models import DealRecord

    owner = make_user("owner", tenant=tenants["acme"])
    rep = make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP)
    director = client_for(owner, "acme")
    _pipeline(director)
    old = _ok(
        client_for(rep, "acme").post(
            "/api/v1/sales/deals/", {"title": "Old", "amount": "1.00"}, format="json"
        ),
        201,
    )
    assert DealRecord.objects.using("tenant_acme").get(id=old["id"]).team_id is None

    dept = Department.objects.create(organization=tenants["acme"].org, name="Sales")
    Membership.objects.filter(user=rep).update(department=dept)
    call_command("assign_record_teams", "--tenant", "acme")
    assert DealRecord.objects.using("tenant_acme").get(id=old["id"]).team_id == dept.id
