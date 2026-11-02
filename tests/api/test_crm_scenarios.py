"""End-to-end business scenarios of the CRM MVP, through the public API only.

A. connecting a company          E. company isolation
B. a sales department at work    F. subscriptions and subscription payments
C. sales management (plans)      G. offboarding an employee
D. department isolation
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.activities.infrastructure.models import ActivityRecord
from modules.organizations.infrastructure.models import Department, Membership
from modules.platform_admin import composition
from modules.sales_pipeline.composition import ensure_starter_pipeline
from modules.tenant_registry.infrastructure.models import TenantDatabase
from platform_core.domain.tenancy import TenantStatus
from platform_core.infrastructure.tenancy.services import system_tenant_scope
from tests.api.test_platform_admin import _company_body, _sign_in
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)


def _today() -> Any:
    return datetime.now(UTC).date()


def _ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.content
    return response.json() if response.content else None


class _Provisioner:
    """Registers the company on a statically configured test database and gives it the
    starter pipeline, as the real provisioner does after CREATE DATABASE + migrate."""

    def provision(self, company_id: UUID, slug: str, requested_by: UUID) -> None:
        TenantDatabase.objects.create(
            organization_id=company_id,
            slug=slug,
            cluster="primary",
            database_name=f"crm_tenant_{slug}",
            status=TenantStatus.ACTIVE.value,
        )
        with system_tenant_scope(slug) as tenant:
            ensure_starter_pipeline(tenant.db_alias)


@pytest.fixture(autouse=True)
def _fresh_throttle() -> None:
    cache.clear()


@pytest.fixture
def admin(make_user: Callable[..., Any], client_for: Callable[..., APIClient]) -> APIClient:
    user = make_user("platform")
    user.is_platform_admin = True
    user.save()
    return client_for(user, None)


@pytest.fixture
def provisioner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(composition, "tenant_provisioner", _Provisioner)


def _in_department(tenant: TenantFixture, name: str, *users: Any) -> str:
    department, _ = Department.objects.get_or_create(organization=tenant.org, name=name)
    Membership.objects.filter(user__in=users, organization=tenant.org).update(department=department)
    return str(department.id)


@pytest.fixture
def company(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> dict[str, Any]:
    return two_department_company(make_user, tenants, client_for)


def two_department_company(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> dict[str, Any]:
    """acme: a director, departments North and South (a head of sales and a manager
    each), a viewer, the standard pipeline."""
    acme = tenants["acme"]
    people = {
        "director": make_user("director", tenant=acme, role=Role.DIRECTOR),
        "rop_north": make_user("rop_north", tenant=acme, role=Role.HEAD_OF_SALES),
        "rep_north": make_user("rep_north", tenant=acme, role=Role.SALES_REP),
        "rop_south": make_user("rop_south", tenant=acme, role=Role.HEAD_OF_SALES),
        "rep_south": make_user("rep_south", tenant=acme, role=Role.SALES_REP),
        "viewer": make_user("viewer", tenant=acme, role=Role.VIEWER),
    }
    north = _in_department(acme, "North", people["rop_north"], people["rep_north"])
    south = _in_department(acme, "South", people["rop_south"], people["rep_south"])
    with system_tenant_scope("acme") as tenant:
        ensure_starter_pipeline(tenant.db_alias)
    clients = {name: client_for(user, "acme") for name, user in people.items()}
    return {"users": people, "c": clients, "north": north, "south": south}


def _customer(client: APIClient, name: str) -> str:
    cid: str = _ok(client.post("/api/v1/customers/", {"name": name}, format="json"), 201)["id"]
    return cid


def _deal(client: APIClient, title: str, **body: Any) -> dict[str, Any]:
    body.update(title=title)
    body.setdefault("amount", "1000.00")
    deal: dict[str, Any] = _ok(client.post("/api/v1/sales/deals/", body, format="json"), 201)
    return deal


def _task(client: APIClient, deal_id: str, title: str, **body: Any) -> Any:
    return client.post(
        "/api/v1/activities/",
        {"kind": "task", "subject_type": "deal", "subject_id": deal_id, "title": title, **body},
        format="json",
    )


def _plan(client: APIClient, **body: Any) -> Any:
    body.setdefault("period", f"{_today():%Y-%m}")
    return client.put("/api/v1/plans/", body, format="json")


# --- A + B: connecting a company, then a sales department at work --------------------


def test_scenario_a_and_b_from_platform_to_a_working_sales_department(
    admin: APIClient, provisioner: None
) -> None:
    # A. The platform administrator connects a company.
    created = _ok(
        admin.post("/api/v1/platform/companies/", _company_body("acme"), format="json"), 201
    )
    assert created["company"]["subscription"]["plan_code"] == "pro"
    director = _sign_in("dir.acme", created["director"]["temporary_password"], "acme")
    pipelines = _ok(director.get("/api/v1/sales/pipelines/"))
    assert [p["name"] for p in pipelines] == ["Основная воронка"]
    stages = pipelines[0]["stages"]
    north = _ok(director.post("/api/v1/departments/", {"name": "North"}, format="json"), 201)
    rop = _ok(
        director.post(
            "/api/v1/staff/",
            {
                "first_name": "Rop",
                "role": "head_of_sales",
                "login": "rop.north",
                "department_id": north["id"],
            },
            format="json",
        ),
        201,
    )
    assert rop["member"]["department"]["id"] == north["id"]
    rop_client = _sign_in("rop.north", rop["temporary_password"], "acme")

    # B. The head of sales requests a manager; the director approves.
    request = _ok(
        rop_client.post(
            "/api/v1/staff/requests/",
            {"kind": "hire", "first_name": "Bakyt", "role": "sales_rep", "login": "bakyt"},
            format="json",
        ),
        201,
    )
    approved = _ok(director.post(f"/api/v1/staff/requests/{request['id']}/approve/"))
    manager = _sign_in("bakyt", approved["temporary_password"], "acme")

    # The manager creates a customer and a deal and sets the next action.
    customer = _customer(manager, "Alpha Trade")
    deal = _deal(manager, "Equipment", customer_id=customer)
    assert deal["stage_id"] == stages[0]["id"]
    task = _ok(_task(manager, deal["id"], "Send the offer", due_date=str(_today())), 201)
    # The head of sales sees the department's deal and its task.
    assert deal["id"] in {d["id"] for d in _ok(rop_client.get("/api/v1/sales/deals/"))["results"]}

    # The manager completes the task and moves the deal on.
    done = _ok(
        manager.post(
            f"/api/v1/activities/{task['id']}/complete/", {"result": "Sent"}, format="json"
        )
    )
    assert done["task_status"] == "completed"
    moved = _ok(
        manager.post(
            f"/api/v1/sales/deals/{deal['id']}/stage/",
            {"stage_id": stages[1]["id"]},
            format="json",
        )
    )
    assert moved["stage_id"] == stages[1]["id"]
    _ok(
        manager.patch(
            f"/api/v1/sales/deals/{deal['id']}/",
            {"amount": "1500.00", "next_step": "Call the decision maker"},
            format="json",
        )
    )

    # Everything is in the history.
    history = _ok(manager.get(f"/api/v1/sales/deals/{deal['id']}/history/"))["results"]
    assert [h["action"] for h in history] == ["deal.updated", "deal.stage_changed", "deal.created"]
    assert set(history[0]["changed_fields"]) == {"amount", "next_step"}
    stage_history = _ok(manager.get(f"/api/v1/sales/deals/{deal['id']}/stage-history/"))
    assert stage_history["results"][-1]["to_stage_id"] == stages[1]["id"]
    timeline = _ok(manager.get(f"/api/v1/activities/?subject_type=deal&subject_id={deal['id']}"))[
        "results"
    ]
    assert [(a["title"], a["task_status"]) for a in timeline] == [("Send the offer", "completed")]


# --- C: sales management --------------------------------------------------------------


def test_scenario_c_plans_and_computed_results(company: dict[str, Any]) -> None:
    c, users = company["c"], company["users"]
    rep_north = str(users["rep_north"].id)
    month = f"{_today():%Y-%m}"

    _ok(_plan(c["director"], scope="company", metric="monthly_sales", target="10"))
    _ok(
        _plan(
            c["director"],
            scope="department",
            scope_id=company["north"],
            metric="monthly_sales",
            target="4",
        )
    )
    _ok(
        _plan(
            c["rop_north"],
            scope="user",
            scope_id=rep_north,
            metric="monthly_turnover",
            target="1000",
        )
    )

    # One sale: the manager's deal gets its first payment this month (seller: the manager).
    deal = _deal(c["rep_north"], "Licences")
    _ok(
        c["director"].post(
            "/api/v1/payments/",
            {
                "deal_id": deal["id"],
                "amount": "400.00",
                "kind": "prepayment",
                "paid_on": str(_today()),
                "seller_user_id": rep_north,
            },
            format="json",
        ),
        201,
    )
    # A call today counts nowhere here (no call plan), a payment of another department
    # does not count for North.
    south_deal = _deal(c["rep_south"], "South deal")
    _ok(
        c["director"].post(
            "/api/v1/payments/",
            {
                "deal_id": south_deal["id"],
                "amount": "900.00",
                "kind": "full",
                "paid_on": str(_today()),
                "seller_user_id": str(users["rep_south"].id),
            },
            format="json",
        ),
        201,
    )

    def progress(client: APIClient) -> dict[tuple[str, str], dict[str, Any]]:
        rows = _ok(client.get(f"/api/v1/plans/progress/?period={month}"))["results"]
        return {(r["scope"], r["metric"]): r for r in rows}

    director_view = progress(c["director"])
    company_sales = director_view[("company", "monthly_sales")]
    assert (company_sales["actual"], company_sales["percent"]) == ("2", "20.0")
    north_sales = director_view[("department", "monthly_sales")]
    assert (north_sales["actual"], north_sales["percent"]) == ("1", "25.0")
    turnover = director_view[("user", "monthly_turnover")]
    assert (turnover["actual"], turnover["percent"], turnover["unit"]) == (
        "400.00",
        "40.0",
        "money",
    )
    assert turnover["currency"] == "KGS"

    # The head of sales sees the company plan, their department's and their manager's.
    assert set(progress(c["rop_north"])) == set(director_view)
    # South's head of sales does not see North's plans.
    assert set(progress(c["rop_south"])) == {("company", "monthly_sales")}
    # The manager sees only their own plan; the viewer sees no plans at all.
    assert set(progress(c["rep_north"])) == {("user", "monthly_turnover")}
    assert c["viewer"].get("/api/v1/plans/progress/").status_code == 403


# --- D: department isolation ------------------------------------------------------------


def test_scenario_d_a_head_of_sales_stays_inside_their_department(
    company: dict[str, Any],
) -> None:
    c, users = company["c"], company["users"]
    rop = c["rop_north"]
    rep_south = str(users["rep_south"].id)
    south_customer = _customer(c["rep_south"], "South client")
    south_deal = _deal(c["rep_south"], "South deal", customer_id=south_customer)
    south_plan = _ok(
        _plan(c["director"], scope="user", scope_id=rep_south, metric="monthly_sales", target="3")
    )

    # Lists hold no data of the other department.
    assert south_deal["id"] not in {
        d["id"] for d in _ok(rop.get("/api/v1/sales/deals/"))["results"]
    }
    assert south_customer not in {x["id"] for x in _ok(rop.get("/api/v1/customers/"))["results"]}
    assert rep_south not in {m["user_id"] for m in _ok(rop.get("/api/v1/staff/"))["results"]}
    # Direct ids are "not found".
    assert rop.get(f"/api/v1/sales/deals/{south_deal['id']}/").status_code == 404
    assert rop.get(f"/api/v1/sales/deals/{south_deal['id']}/history/").status_code == 404
    assert rop.get(f"/api/v1/customers/{south_customer}/").status_code == 404
    assert rop.post(f"/api/v1/customers/{south_customer}/archive/").status_code == 404
    assert rop.get(f"/api/v1/plans/{south_plan['id']}/history/").status_code == 404
    # No work handed to the other department.
    own_deal = _deal(rop, "North deal")
    assert _task(rop, own_deal["id"], "Call", assignee_user_id=rep_south).status_code == 403
    assert (
        rop.post(
            f"/api/v1/sales/deals/{own_deal['id']}/assign/",
            {"owner_user_id": rep_south},
            format="json",
        ).status_code
        == 403
    )
    assert _task(rop, south_deal["id"], "Sneak").status_code == 404  # cannot link to it
    # No plans or absences outside the department.
    assert (
        _plan(rop, scope="user", scope_id=rep_south, metric="monthly_sales", target="9").status_code
        == 403
    )
    absence = {"kind": "vacation", "start_date": "2026-12-01", "end_date": "2026-12-02"}
    assert (
        rop.post("/api/v1/absences/", {**absence, "user_id": rep_south}, format="json").status_code
        == 403
    )

    # The director reaches both departments.
    director = c["director"]
    assert director.get(f"/api/v1/sales/deals/{south_deal['id']}/").status_code == 200
    _ok(director.get(f"/api/v1/plans/{south_plan['id']}/history/"))
    _ok(
        director.post(
            f"/api/v1/sales/deals/{own_deal['id']}/assign/",
            {"owner_user_id": rep_south},
            format="json",
        )
    )
    moved = _ok(director.get(f"/api/v1/sales/deals/{own_deal['id']}/"))
    assert moved["owner_user_id"] == rep_south
    # It now belongs to South: North's head of sales no longer sees it.
    assert rop.get(f"/api/v1/sales/deals/{own_deal['id']}/").status_code == 404
    _ok(director.post("/api/v1/absences/", {**absence, "user_id": rep_south}, format="json"), 201)


# --- E: company isolation -----------------------------------------------------------------


def test_scenario_e_companies_never_see_each_other(
    company: dict[str, Any],
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    acme_director = company["c"]["director"]
    acme_rep = company["users"]["rep_north"]
    acme_customer = _customer(acme_director, "Acme secret client")
    acme_deal = _deal(acme_director, "Acme secret deal", customer_id=acme_customer)

    outsider = make_user("globex_director", tenant=tenants["globex"], role=Role.DIRECTOR)
    globex = client_for(outsider, "globex")
    with system_tenant_scope("globex") as tenant:
        ensure_starter_pipeline(tenant.db_alias)

    # Lists, search and filters return nothing of acme.
    assert _ok(globex.get("/api/v1/customers/?search=secret"))["count"] == 0
    assert _ok(globex.get(f"/api/v1/customers/?owner_id={acme_rep.id}"))["count"] == 0
    assert _ok(globex.get("/api/v1/sales/deals/?search=secret"))["count"] == 0
    assert _ok(globex.get(f"/api/v1/sales/deals/?customer_id={acme_customer}"))["count"] == 0
    # Direct ids of acme are unknown in globex.
    assert globex.get(f"/api/v1/customers/{acme_customer}/").status_code == 404
    assert globex.get(f"/api/v1/sales/deals/{acme_deal['id']}/").status_code == 404
    assert _task(globex, acme_deal["id"], "Sneak").status_code == 404
    linked = globex.post(
        "/api/v1/sales/deals/",
        {"title": "Steal", "amount": "1.00", "customer_id": acme_customer},
        format="json",
    )
    assert linked.status_code == 404
    # Work cannot be handed to another company's member.
    own = _deal(globex, "Globex deal")
    assert (
        globex.post(
            f"/api/v1/sales/deals/{own['id']}/assign/",
            {"owner_user_id": str(acme_rep.id)},
            format="json",
        ).status_code
        == 400
    )
    # Switching the header to acme does not open it.
    as_acme = client_for(outsider, "acme")
    assert as_acme.get("/api/v1/customers/").status_code == 404
    assert as_acme.get(f"/api/v1/sales/deals/{acme_deal['id']}/").status_code == 404


# --- F: subscriptions and payments ----------------------------------------------------------


def test_scenario_f_subscription_access_and_confirmed_revenue(
    admin: APIClient, provisioner: None
) -> None:
    created = _ok(
        admin.post(
            "/api/v1/platform/companies/",
            _company_body("acme", plan_code="free", features=["telephony"]),
            format="json",
        ),
        201,
    )
    company_id = created["company"]["id"]
    director = _sign_in("dir.acme", created["director"]["temporary_password"], "acme")
    # Active subscription: core and switched-on modules work, others are refused.
    assert director.get("/api/v1/customers/").status_code == 200
    assert director.get("/api/v1/telephony/calls/").status_code == 200
    assert director.get("/api/v1/payments/").json()["error"]["code"] == "feature_not_in_plan"

    payments_url = f"/api/v1/platform/companies/{company_id}/payments/"
    body = {
        "amount": "49.00",
        "currency": "USD",
        "paid_on": str(_today()),
        "reference": "BANK-0001",
    }
    pending = _ok(admin.post(payments_url, body, format="json"), 201)
    assert pending["status"] == "pending"

    def received() -> list[dict[str, str]]:
        revenue: list[dict[str, str]] = _ok(admin.get("/api/v1/platform/overview/"))["revenue"][
            "received"
        ]
        return revenue

    assert received() == []  # recorded is not received
    _ok(admin.post(f"/api/v1/platform/payments/{pending['id']}/confirm/"))
    assert received() == [{"currency": "USD", "amount": "49.00"}]
    # The same external operation cannot be recorded twice; a decision is final.
    duplicate = admin.post(payments_url, body, format="json")
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "duplicate_payment"
    assert admin.post(f"/api/v1/platform/payments/{pending['id']}/cancel/").status_code == 409
    # Older and cancelled payments do not count for the period.
    old = {**body, "reference": "BANK-OLD", "paid_on": str(_today() - timedelta(days=60))}
    _ok(admin.post(payments_url, {**old, "confirm": True}, format="json"), 201)
    mistake = _ok(admin.post(payments_url, {**body, "reference": "BANK-X"}, format="json"), 201)
    _ok(
        admin.post(
            f"/api/v1/platform/payments/{mistake['id']}/cancel/",
            {"note": "Never arrived"},
            format="json",
        )
    )
    assert received() == [{"currency": "USD", "amount": "49.00"}]
    year = _ok(admin.get("/api/v1/platform/overview/?period_days=365"))["revenue"]["received"]
    assert year == [{"currency": "USD", "amount": "98.00"}]
    future = {**body, "reference": "BANK-F", "paid_on": str(_today() + timedelta(days=3))}
    assert admin.post(payments_url, future, format="json").status_code == 400
    history = _ok(admin.get(payments_url))
    assert history["count"] == 3
    assert {p["status"] for p in history["results"]} == {"confirmed", "cancelled"}
    # Only the platform administrator handles subscription payments.
    assert director.get("/api/v1/platform/payments/").status_code == 403
    assert director.post(payments_url, body, format="json").status_code == 403

    # Expired subscription: business data closed.
    _ok(
        admin.put(
            f"/api/v1/platform/companies/{company_id}/subscription/",
            {
                "plan_code": "free",
                "starts_on": str(_today() - timedelta(days=40)),
                "ends_on": str(_today() - timedelta(days=1)),
                "features": ["telephony"],
            },
            format="json",
        )
    )
    assert director.get("/api/v1/customers/").json()["error"]["code"] == "subscription_inactive"
    # Suspended company: closed entirely.
    _ok(
        admin.patch(
            f"/api/v1/platform/companies/{company_id}/", {"is_active": False}, format="json"
        )
    )
    assert director.get("/api/v1/subscription/").status_code == 404


# --- G: offboarding -----------------------------------------------------------------------------


def test_scenario_g_dismissal_keeps_history_and_hands_over_open_work(
    company: dict[str, Any],
) -> None:
    c, users = company["c"], company["users"]
    rep, rop = users["rep_north"], users["rop_north"]
    deal = _deal(c["rep_north"], "Rep's deal")
    open_a = _ok(_task(c["rep_north"], deal["id"], "Call back"), 201)
    other_deal = _deal(c["rep_north"], "Second deal")
    open_b = _ok(_task(c["rep_north"], other_deal["id"], "Send contract"), 201)
    done = _ok(_task(c["rep_north"], other_deal["id"], "Intro call"), 201)
    _ok(c["rep_north"].post(f"/api/v1/activities/{done['id']}/complete/"))
    won = _deal(c["rep_north"], "Already won")
    _ok(c["rep_north"].post(f"/api/v1/sales/deals/{won['id']}/win/"))
    note = _ok(
        c["rep_north"].post(
            "/api/v1/activities/",
            {"kind": "note", "subject_type": "deal", "subject_id": deal["id"], "body": "Met"},
            format="json",
        ),
        201,
    )

    membership = Membership.objects.get(user=rep)
    request = _ok(
        c["rop_north"].post(
            "/api/v1/staff/requests/",
            {"kind": "dismiss", "membership_id": str(membership.id), "reason": "Left"},
            format="json",
        ),
        201,
    )
    # A second request for the same person is refused while the first is pending.
    again = c["rop_north"].post(
        "/api/v1/staff/requests/",
        {"kind": "dismiss", "membership_id": str(membership.id)},
        format="json",
    )
    assert again.status_code == 409
    # Nothing happens before the director's decision.
    assert c["rep_north"].get("/api/v1/customers/").status_code == 200
    decided = _ok(c["director"].post(f"/api/v1/staff/requests/{request['id']}/approve/"))
    assert decided["request"]["status"] == "approved"
    assert decided["request"]["decided_by"]
    assert c["director"].post(f"/api/v1/staff/requests/{request['id']}/approve/").status_code == 409

    # The person loses access; the account and the records stay.
    assert c["rep_north"].get("/api/v1/customers/").status_code == 401
    assert get_user_model().objects.filter(id=rep.id, is_active=False).exists()
    rows = ActivityRecord.objects.using("tenant_acme")
    for task_id in (open_a["id"], open_b["id"]):
        assert rows.get(id=task_id).assignee_user_id == rop.id  # handed to the requester
    assert rows.get(id=done["id"]).assignee_user_id == rep.id  # history unchanged
    assert rows.get(id=note["id"]).owner_user_id == rep.id  # authorship unchanged
    # Open deals go to the requester (audited); a closed deal keeps its owner.
    for open_deal in (deal, other_deal):
        moved = _ok(c["director"].get(f"/api/v1/sales/deals/{open_deal['id']}/"))
        assert moved["owner_user_id"] == str(rop.id)
        history = _ok(c["director"].get(f"/api/v1/sales/deals/{open_deal['id']}/history/"))
        assert history["results"][0]["action"] == "deal.reassigned"
    kept = _ok(c["director"].get(f"/api/v1/sales/deals/{won['id']}/"))
    assert kept["owner_user_id"] == str(rep.id)
    # Nobody hands work to the deactivated member any more.
    refused = c["director"].post(
        f"/api/v1/sales/deals/{deal['id']}/assign/",
        {"owner_user_id": str(rep.id)},
        format="json",
    )
    assert refused.status_code == 400


def test_dismissal_falls_back_to_the_director_when_the_requester_has_left(
    company: dict[str, Any],
) -> None:
    c, users = company["c"], company["users"]
    task = _ok(_task(c["rep_north"], _deal(c["rep_north"], "Open")["id"], "Follow up"), 201)
    rep_membership = Membership.objects.get(user=users["rep_north"])
    request = _ok(
        c["rop_north"].post(
            "/api/v1/staff/requests/",
            {"kind": "dismiss", "membership_id": str(rep_membership.id)},
            format="json",
        ),
        201,
    )
    # The requesting head of sales leaves before the decision.
    rop_membership = Membership.objects.get(user=users["rop_north"])
    _ok(c["director"].post(f"/api/v1/staff/{rop_membership.id}/deactivate/"))
    _ok(c["director"].post(f"/api/v1/staff/requests/{request['id']}/approve/"))
    moved = ActivityRecord.objects.using("tenant_acme").get(id=task["id"])
    assert moved.assignee_user_id == users["director"].id  # never an inactive account


# --- pipeline configuration and customers ------------------------------------------------


def test_pipeline_stages_are_configured_without_breaking_deals(company: dict[str, Any]) -> None:
    director = company["c"]["director"]
    (pipeline,) = _ok(director.get("/api/v1/sales/pipelines/"))
    url = f"/api/v1/sales/pipelines/{pipeline['id']}/"
    first, second = pipeline["stages"][0]["id"], pipeline["stages"][1]["id"]

    added = _ok(
        director.post(
            f"{url}stages/", {"name": "Demo", "probability": 45, "position": 2}, format="json"
        ),
        201,
    )
    assert [s["name"] for s in added["stages"]][:4] == [
        "Новый",
        "Квалификация",
        "Demo",
        "Предложение",
    ]
    duplicate = director.post(f"{url}stages/", {"name": "demo"}, format="json")
    assert duplicate.status_code == 400
    renamed = _ok(
        director.patch(
            f"{url}stages/{second}/", {"name": "Qualified", "probability": 35}, format="json"
        )
    )
    assert renamed["stages"][1]["name"] == "Qualified"
    order = [s["id"] for s in renamed["stages"]]
    reordered = _ok(
        director.put(
            f"{url}stage-order/", {"stage_ids": [order[1], order[0], *order[2:]]}, format="json"
        )
    )
    assert [s["id"] for s in reordered["stages"]][:2] == [second, first]
    assert (
        director.put(f"{url}stage-order/", {"stage_ids": order[:2]}, format="json").status_code
        == 400
    )

    # A stage with an open deal cannot be archived; once empty it can, and moves to it stop.
    # (After the reorder `second` is the first stage, so new deals start there.)
    deal = _deal(director, "On second")
    assert deal["stage_id"] == second
    busy = director.patch(f"{url}stages/{second}/", {"is_active": False}, format="json")
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "stage_in_use"
    _ok(
        director.post(
            f"/api/v1/sales/deals/{deal['id']}/stage/", {"stage_id": first}, format="json"
        )
    )
    archived = _ok(director.patch(f"{url}stages/{second}/", {"is_active": False}, format="json"))
    assert {s["id"]: s["is_active"] for s in archived["stages"]}[second] is False
    blocked = director.post(
        f"/api/v1/sales/deals/{deal['id']}/stage/", {"stage_id": second}, format="json"
    )
    assert blocked.status_code == 400
    # The deal's stage history still names the archived stage.
    history = _ok(director.get(f"/api/v1/sales/deals/{deal['id']}/stage-history/"))["results"]
    assert second in {h["from_stage_id"] for h in history}
    renamed_pipeline = _ok(director.patch(url, {"name": "Main"}, format="json"))
    assert renamed_pipeline["name"] == "Main" and renamed_pipeline["is_default"] is True
    # Pipelines belong to the company: a manager cannot change them.
    rep = company["c"]["rep_north"]
    assert rep.patch(url, {"name": "Mine"}, format="json").status_code == 403
    assert rep.post(f"{url}stages/", {"name": "X"}, format="json").status_code == 403


def test_customers_archive_filter_and_handover(company: dict[str, Any]) -> None:
    c, users = company["c"], company["users"]
    rep = c["rep_north"]
    alpha = _customer(rep, "Alpha")
    beta = _customer(rep, "Beta")
    old_deal = _deal(rep, "Beta's old deal", customer_id=beta)
    note = _ok(
        rep.post(
            "/api/v1/activities/",
            {"kind": "note", "subject_type": "customer", "subject_id": beta, "body": "Met"},
            format="json",
        ),
        201,
    )
    _ok(rep.post(f"/api/v1/customers/{beta}/archive/"))
    # Archiving keeps the card, its deals and its history readable.
    assert _ok(rep.get(f"/api/v1/customers/{beta}/"))["archived_at"]
    assert _ok(rep.get(f"/api/v1/sales/deals/{old_deal['id']}/"))["customer_id"] == beta
    timeline = _ok(rep.get(f"/api/v1/activities/?subject_type=customer&subject_id={beta}"))
    assert [a["id"] for a in timeline["results"]] == [note["id"]]
    deals = _ok(c["director"].get(f"/api/v1/sales/deals/?customer_id={beta}"))["results"]
    assert [d["id"] for d in deals] == [old_deal["id"]]

    assert [x["name"] for x in _ok(rep.get("/api/v1/customers/"))["results"]] == ["Alpha"]
    archived = _ok(rep.get("/api/v1/customers/?archived=true"))["results"]
    assert [x["name"] for x in archived] == ["Beta"] and archived[0]["archived_at"]
    # An archived customer is read-only and takes no new deals until restored.
    assert rep.patch(f"/api/v1/customers/{beta}/", {"name": "B2"}, format="json").status_code == 409
    no_deal = rep.post(
        "/api/v1/sales/deals/", {"title": "X", "amount": "1.00", "customer_id": beta}, format="json"
    )
    assert no_deal.status_code == 400
    _ok(rep.post(f"/api/v1/customers/{beta}/restore/"))
    _deal(rep, "Beta deal", customer_id=beta)
    assert [
        x["name"] for x in _ok(rep.get("/api/v1/customers/?archived=all&ordering=name"))["results"]
    ] == ["Alpha", "Beta"]

    # Handover: a manager cannot, the head of sales within the department, director anywhere.
    rop_north = str(users["rop_north"].id)
    refused = rep.post(
        f"/api/v1/customers/{alpha}/assign/", {"owner_user_id": rop_north}, format="json"
    )
    assert refused.status_code == 403
    rep_south = str(users["rep_south"].id)
    cross = c["rop_north"].post(
        f"/api/v1/customers/{alpha}/assign/", {"owner_user_id": rep_south}, format="json"
    )
    assert cross.status_code == 403
    _ok(
        c["director"].post(
            f"/api/v1/customers/{alpha}/assign/", {"owner_user_id": rep_south}, format="json"
        )
    )
    by_owner = _ok(c["director"].get(f"/api/v1/customers/?owner_id={rep_south}"))["results"]
    assert [x["id"] for x in by_owner] == [alpha]
    assert rep.get(f"/api/v1/customers/{alpha}/").status_code == 404  # no longer theirs


def test_failed_background_task_is_recorded_without_its_arguments() -> None:
    from modules.platform_admin.infrastructure.models import ErrorEvent
    from platform_core.infrastructure.observability.celery_signals import _on_failure

    class _Task:
        name = "ai_insights.submit_report"

        def run(self, tenant_slug: str, report_id: str) -> None: ...

    _on_failure(
        sender=_Task(),
        exception=RuntimeError("token=abc secret"),
        args=("acme", "r-1"),
        kwargs={},
    )
    event = ErrorEvent.objects.get()
    assert (event.view, event.method, event.tenant_slug) == (
        "ai_insights.submit_report",
        "TASK",
        "acme",
    )
    assert event.exception_type == "RuntimeError"
    assert "secret" not in str(vars(event)) and "r-1" not in str(vars(event))
