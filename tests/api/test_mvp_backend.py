"""MVP backend rules added on top of the CRM core: leads, analytics, working time.

Uses the two-department company of the scenario suite (North and South, a head of
sales and a manager each, a director and a viewer).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.activities.infrastructure.models import ActivityRecord
from modules.organizations.infrastructure.models import Membership
from modules.planning.infrastructure.models import WorkSessionRecord
from tests.api.payment_helpers import pay_confirmed
from tests.api.sale_helpers import close_as_sale
from tests.api.test_crm_scenarios import _customer, _deal, _ok, _task, two_department_company
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)


@pytest.fixture
def company(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> dict[str, Any]:
    return two_department_company(make_user, tenants, client_for)


def _today() -> Any:
    return datetime.now(UTC).date()


def _lead(client: APIClient, name: str, **body: Any) -> dict[str, Any]:
    body.setdefault("phone", "+996555000111")
    lead: dict[str, Any] = _ok(
        client.post("/api/v1/leads/", {"name": name, **body}, format="json"), 201
    )
    return lead


def _pay(client: APIClient, verifier: APIClient, **body: Any) -> dict[str, Any]:
    """A payment with its receipt, confirmed by ``verifier`` (only confirmed ones count)."""
    body.setdefault("paid_on", str(_today()))
    return pay_confirmed(client, verifier, **body)


# --- leads ------------------------------------------------------------------------------


def test_lead_converts_into_an_existing_customer_without_duplicates(
    company: dict[str, Any],
) -> None:
    rep = company["c"]["rep_north"]
    existing = _customer(rep, "Alpha Trade")
    lead = _lead(rep, "Alpha again")
    _ok(rep.post(f"/api/v1/leads/{lead['id']}/qualify/"))
    body = {
        "customer_id": existing,
        "create_deal": {"title": "Repeat order", "amount": "500.00"},
    }
    first = _ok(rep.post(f"/api/v1/leads/{lead['id']}/convert/", body, format="json"))
    assert first["customer_id"] == existing and first["deal_id"]
    # A repeated call returns the same result and opens no second deal.
    again = _ok(rep.post(f"/api/v1/leads/{lead['id']}/convert/", body, format="json"))
    assert again["already_converted"] is True and again["deal_id"] is None
    assert _ok(rep.get("/api/v1/customers/?search=Alpha"))["count"] == 1
    deals = _ok(rep.get(f"/api/v1/sales/deals/?customer_id={existing}"))["results"]
    assert [d["id"] for d in deals] == [first["deal_id"]]
    # The lead stays, converted, with its history.
    kept = _ok(rep.get(f"/api/v1/leads/{lead['id']}/"))
    assert kept["status"] == "converted" and kept["converted_customer_id"] == existing
    history = [h["action"] for h in _ok(rep.get(f"/api/v1/leads/{lead['id']}/history/"))["results"]]
    assert history == ["lead.converted", "lead.qualified", "lead.captured"]
    # A customer the caller cannot see is "not found"; no customer is created instead.
    south_customer = _customer(company["c"]["rep_south"], "South client")
    other = _lead(rep, "Other")
    _ok(rep.post(f"/api/v1/leads/{other['id']}/qualify/"))
    refused = rep.post(
        f"/api/v1/leads/{other['id']}/convert/", {"customer_id": south_customer}, format="json"
    )
    assert refused.status_code == 404
    assert _ok(rep.get(f"/api/v1/leads/{other['id']}/"))["status"] == "qualified"


def test_a_failed_deal_leaves_the_lead_unconverted(company: dict[str, Any]) -> None:
    rep = company["c"]["rep_north"]
    lead = _lead(rep, "Beta")
    _ok(rep.post(f"/api/v1/leads/{lead['id']}/qualify/"))
    broken = rep.post(
        f"/api/v1/leads/{lead['id']}/convert/",
        {"create_deal": {"title": "X", "amount": "1.00", "pipeline_id": str(UUID(int=1))}},
        format="json",
    )
    assert broken.status_code == 400, broken.content  # unknown pipeline
    assert _ok(rep.get(f"/api/v1/leads/{lead['id']}/"))["status"] == "qualified"
    assert _ok(rep.get("/api/v1/customers/?search=Beta"))["count"] == 0


def test_leads_are_searched_filtered_and_handed_over(company: dict[str, Any]) -> None:
    c, users = company["c"], company["users"]
    rep = c["rep_north"]
    _lead(rep, "Zeta", phone="+996555000222", source="website")
    alpha = _lead(rep, "Alpha", phone="+996555000333")
    assert [x["name"] for x in _ok(rep.get("/api/v1/leads/?ordering=name"))["results"]] == [
        "Alpha",
        "Zeta",
    ]
    assert [x["name"] for x in _ok(rep.get("/api/v1/leads/?search=000333"))["results"]] == ["Alpha"]
    assert [x["name"] for x in _ok(rep.get("/api/v1/leads/?source=website"))["results"]] == ["Zeta"]
    today = str(_today())
    assert _ok(rep.get(f"/api/v1/leads/?created_from={today}&created_to={today}"))["count"] == 2

    rop_north, rep_south = str(users["rop_north"].id), str(users["rep_south"].id)
    url = f"/api/v1/leads/{alpha['id']}/assign/"
    assert rep.post(url, {"owner_user_id": rop_north}, format="json").status_code == 403
    assert c["rop_north"].post(url, {"owner_user_id": rep_south}, format="json").status_code == 403
    moved = _ok(c["director"].post(url, {"owner_user_id": rep_south}, format="json"))
    assert moved["owner_user_id"] == rep_south
    assert rep.get(f"/api/v1/leads/{alpha['id']}/").status_code == 404
    history = _ok(c["director"].get(f"/api/v1/leads/{alpha['id']}/history/"))["results"]
    assert history[0]["action"] == "lead.reassigned" and history[0]["changed_fields"] == ["owner"]


def test_one_customer_has_independent_deals(company: dict[str, Any]) -> None:
    rep = company["c"]["rep_north"]
    customer = _customer(rep, "Returning client")
    first = _deal(rep, "First order", customer_id=customer)
    second = _deal(rep, "Second order", customer_id=customer)
    close_as_sale(first["id"])
    still_open = _ok(rep.get(f"/api/v1/sales/deals/{second['id']}/"))
    assert still_open["status"] == "open" and still_open["customer_id"] == customer
    listed = _ok(rep.get(f"/api/v1/sales/deals/?customer_id={customer}"))
    assert listed["count"] == 2


# --- tasks and meetings filters --------------------------------------------------------------


def test_tasks_filter_by_assignee_due_date_and_overdue(company: dict[str, Any]) -> None:
    c, users = company["c"], company["users"]
    rop = c["rop_north"]
    deal = _deal(rop, "Filters")
    rep_id = str(users["rep_north"].id)
    yesterday = str(_today() - timedelta(days=1))
    late = _ok(_task(rop, deal["id"], "Late", assignee_user_id=rep_id, due_date=yesterday), 201)
    _ok(_task(rop, deal["id"], "No deadline", priority="urgent"), 201)

    def titles(query: str) -> set[str]:
        return {
            a["title"] for a in _ok(rop.get(f"/api/v1/activities/?kind=task&{query}"))["results"]
        }

    assert titles(f"assignee_id={rep_id}") == {"Late"}
    assert titles("overdue=true") == {"Late"}
    assert titles("no_due_date=true") == {"No deadline"}
    assert titles("priority=urgent") == {"No deadline"}
    assert titles(f"due_from={yesterday}&due_to={yesterday}") == {"Late"}
    _ok(c["rep_north"].post(f"/api/v1/activities/{late['id']}/complete/"))
    assert titles("overdue=true") == set()


def test_meetings_filter_by_period_and_responsible(company: dict[str, Any]) -> None:
    c, users = company["c"], company["users"]
    start = datetime.now(UTC) + timedelta(days=2)
    for client in (c["rep_north"], c["rop_north"]):
        _ok(
            client.post(
                "/api/v1/meetings/",
                {
                    "title": "Consultation",
                    "scheduled_start": start.isoformat(),
                    "scheduled_end": (start + timedelta(hours=1)).isoformat(),
                },
                format="json",
            ),
            201,
        )
    rop = c["rop_north"]
    rep_id = str(users["rep_north"].id)
    mine = _ok(rop.get(f"/api/v1/meetings/?owner_id={rep_id}"))["results"]
    assert {m["owner_user_id"] for m in mine} == {rep_id}
    day = start.astimezone(UTC).date()
    window = (day - timedelta(days=1), day + timedelta(days=1))
    in_period = rop.get(f"/api/v1/meetings/?date_from={window[0]}&date_to={window[1]}")
    assert _ok(in_period)["count"] == 2
    past = rop.get(f"/api/v1/meetings/?date_to={_today() - timedelta(days=1)}")
    assert _ok(past)["count"] == 0


# --- analytics -------------------------------------------------------------------------------


def test_every_new_deal_is_a_sale_and_new_clients_are_counted_apart(
    company: dict[str, Any],
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    """Owner decision 02.11.2026: each new deal is a sale, even for a returning client."""
    c, users = company["c"], company["users"]
    # A payment without a deal has no department: another director confirms it.
    second_director = client_for(
        make_user("director2", tenant=tenants["acme"], role=Role.DIRECTOR), "acme"
    )
    director, rep, rep_id = c["director"], c["rep_north"], str(users["rep_north"].id)
    month = f"{_today():%Y-%m}"
    earlier = _today().replace(day=1) - timedelta(days=5)

    def pay(deal: dict[str, Any] | None, amount: str, **extra: Any) -> None:
        body = {"amount": amount, "seller_user_id": rep_id, **extra}
        if deal is not None:
            body["deal_id"] = deal["id"]
        # The head of sales confirms payments on the department's deals.
        _pay(director, c["rop_north"] if deal is not None else second_director, **body)

    client = _customer(rep, "Returning client")
    first = _deal(rep, "Order", customer_id=client)
    pay(first, "100.00")  # partial prepayment...
    pay(first, "400.00", kind="additional")  # ...and the rest: still ONE sale
    second = _deal(rep, "Second order", customer_id=client)
    pay(second, "300.00", kind="full")  # the same client again: a SECOND sale

    old_client = _customer(rep, "Paid before")
    old_deal = _deal(rep, "Old", customer_id=old_client)
    pay(old_deal, "50.00", paid_on=str(earlier))
    pay(old_deal, "70.00", kind="additional")  # first payment was last month: no sale now

    refunded = _deal(rep, "Refunded", customer_id=_customer(rep, "Changed mind"))
    pay(refunded, "200.00")
    pay(refunded, "200.00", is_refund=True)  # fully refunded: not a sale

    lost = _deal(rep, "Lost but paid", customer_id=_customer(rep, "Kept prepayment"))
    pay(lost, "80.00")
    _ok(rep.post(f"/api/v1/sales/deals/{lost['id']}/lose/", {"reason": "Expensive"}, format="json"))
    _ok(rep.post(f"/api/v1/sales/deals/{lost['id']}/reopen/"))  # never counted twice

    pay(None, "30.00", customer_id=_customer(rep, "No deal"))  # money, not a sale

    _ok(
        director.put(
            "/api/v1/plans/",
            {
                "scope": "user",
                "scope_id": rep_id,
                "metric": "monthly_sales",
                "period": month,
                "target": "4",
            },
            format="json",
        )
    )
    (sales,) = _ok(director.get(f"/api/v1/plans/progress/?scope_id={rep_id}&daily=true"))["results"]
    assert (sales["actual"], sales["percent"], sales["remaining"]) == ("3", "75.0", "1.00")
    assert sales["daily"][-1] == {"date": str(_today()), "actual": "3"}
    assert len(sales["daily"]) == _today().day

    # Every report counts sales the same way; new clients are a separate figure.
    metrics = _ok(director.get("/api/v1/payments/metrics/"))
    assert metrics["sales_count"] == 3
    # Returning client, "Changed mind", "Kept prepayment", "No deal" — "Paid before" is old.
    assert metrics["new_clients"] == 4
    assert metrics["turnover"] == str(
        Decimal(first["amount"]) + Decimal(second["amount"]) + Decimal(lost["amount"])
    )
    by_seller = _ok(director.get("/api/v1/payments/metrics/?group_by=seller"))["by_seller"]
    assert by_seller == [{"seller_user_id": rep_id, "sales_count": 3, "new_clients": 4}]
    today = str(_today())
    day = _ok(director.get(f"/api/v1/payments/metrics/?paid_from={today}&paid_to={today}"))
    assert day["sales_count"] == 3
    cabinet = _ok(director.get(f"/api/v1/team/plan-fact/?month={month}"))["rows"]
    assert {r["user_id"]: r["sales_actual"] for r in cabinet}[rep_id] == 3
    # A manager sees the same definition applied to their own payments only.
    assert _ok(c["rep_south"].get("/api/v1/payments/metrics/"))["sales_count"] == 0


def test_lead_and_customer_counts_follow_the_callers_scope(company: dict[str, Any]) -> None:
    c, users = company["c"], company["users"]
    _lead(c["rep_north"], "N1")
    _lead(c["rep_north"], "N2", phone="+996555000444")
    _lead(c["rep_south"], "S1", phone="+996555000555")
    _customer(c["rep_north"], "North client")
    archived = _customer(c["rep_south"], "South client")
    _ok(c["rep_south"].post(f"/api/v1/customers/{archived}/archive/"))

    def counts(client: APIClient, group_by: str = "company") -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = _ok(
            client.get(f"/api/v1/analytics/kpis/crm-counts/?group_by={group_by}")
        )["results"]
        return rows

    (total,) = counts(c["director"])
    assert (total["leads_total"], total["leads_new"]) == (3, 3)
    assert (total["customers_total"], total["customers_active"], total["customers_new"]) == (
        2,
        1,
        2,
    )
    by_department = {r["group_id"]: r for r in counts(c["director"], "department")}
    assert by_department[company["north"]]["leads_total"] == 2
    assert by_department[company["south"]]["customers_active"] == 0
    # A head of sales counts their department only; a manager their own records.
    (north,) = counts(c["rop_north"])
    assert (north["leads_total"], north["customers_total"]) == (2, 1)
    by_user = {r["group_id"]: r["leads_total"] for r in counts(c["rep_north"], "user")}
    assert by_user == {str(users["rep_north"].id): 2}
    # Period filter: nothing created yesterday.
    yesterday = str(_today() - timedelta(days=1))
    (old,) = _ok(
        c["director"].get(
            f"/api/v1/analytics/kpis/crm-counts/?date_from={yesterday}&date_to={yesterday}"
        )
    )["results"]
    assert (old["leads_new"], old["leads_total"]) == (0, 3)


# --- working time ---------------------------------------------------------------------------


def test_working_sessions_start_stop_and_history(company: dict[str, Any]) -> None:
    c, users = company["c"], company["users"]
    rep = c["rep_north"]
    assert _ok(rep.get("/api/v1/work-sessions/current/"))["session"] is None
    started = _ok(rep.post("/api/v1/work-sessions/start/"), 201)
    assert started["status"] == "open" and started["ended_at"] is None
    assert rep.post("/api/v1/work-sessions/start/").status_code == 409
    assert _ok(rep.get("/api/v1/work-sessions/current/"))["session"]["id"] == started["id"]
    stopped = _ok(rep.post("/api/v1/work-sessions/stop/"))
    assert stopped["status"] == "closed" and stopped["duration_seconds"] >= 0
    assert rep.post("/api/v1/work-sessions/stop/").status_code == 409

    # Visibility: own; the head of sales their department; the director everyone.
    _ok(c["rep_south"].post("/api/v1/work-sessions/start/"), 201)
    rep_id, south_id = str(users["rep_north"].id), str(users["rep_south"].id)
    assert {s["user_id"] for s in _ok(rep.get("/api/v1/work-sessions/"))["results"]} == {rep_id}
    assert rep.get(f"/api/v1/work-sessions/?user_id={south_id}").status_code == 403
    rop_view = {s["user_id"] for s in _ok(c["rop_north"].get("/api/v1/work-sessions/"))["results"]}
    assert rop_view == {rep_id}
    assert c["rop_north"].get(f"/api/v1/work-sessions/?user_id={south_id}").status_code == 403
    director_view = _ok(c["director"].get("/api/v1/work-sessions/?status=open"))["results"]
    assert [s["user_id"] for s in director_view] == [south_id]


def test_a_forgotten_session_becomes_unconfirmed_never_back_dated(
    company: dict[str, Any],
) -> None:
    from entrypoints.workers.tasks.reports import settle_stale_work_sessions

    c = company["c"]
    first = _ok(c["rep_north"].post("/api/v1/work-sessions/start/"), 201)
    other = _ok(c["rep_south"].post("/api/v1/work-sessions/start/"), 201)
    long_ago = datetime.now(UTC) - timedelta(hours=20)
    WorkSessionRecord.objects.using("tenant_acme").filter(id__in=[first["id"], other["id"]]).update(
        started_at=long_ago
    )
    # Lazily on the next start: the old one is settled, a new one opens.
    second = _ok(c["rep_north"].post("/api/v1/work-sessions/start/"), 201)
    assert second["id"] != first["id"]
    old = WorkSessionRecord.objects.using("tenant_acme").get(id=first["id"])
    assert old.status == "unconfirmed" and old.ended_at is None and old.settled_at is not None
    # And by the hourly job for everyone else.
    settle_stale_work_sessions()
    south = WorkSessionRecord.objects.using("tenant_acme").get(id=other["id"])
    assert south.status == "unconfirmed" and south.ended_at is None
    history = _ok(c["director"].get("/api/v1/work-sessions/?status=unconfirmed"))["results"]
    assert {h["duration_seconds"] for h in history} == {None}


# --- direct dismissal by a head of sales ------------------------------------------------------


def test_head_of_sales_dismisses_a_manager_and_takes_over_open_work(
    company: dict[str, Any],
) -> None:
    c, users = company["c"], company["users"]
    deal = _deal(c["rep_north"], "Open deal")
    task = _ok(_task(c["rep_north"], deal["id"], "Call"), 201)
    membership = Membership.objects.get(user=users["rep_north"])
    _ok(c["rop_north"].post(f"/api/v1/staff/{membership.id}/deactivate/"))
    rop_id = users["rop_north"].id
    assert ActivityRecord.objects.using("tenant_acme").get(id=task["id"]).assignee_user_id == rop_id
    # The deal stays with the dismissed manager until reassigned by hand.
    rep_id = str(users["rep_north"].id)
    assert _ok(c["rop_north"].get(f"/api/v1/sales/deals/{deal['id']}/"))["owner_user_id"] == rep_id
    assert c["rep_north"].get("/api/v1/customers/").status_code == 401


def test_a_refund_in_another_currency_does_not_cancel_the_sale(company: dict[str, Any]) -> None:
    c, users = company["c"], company["users"]
    rep_id = str(users["rep_north"].id)
    deal = _deal(c["rep_north"], "Mixed currencies")
    _pay(
        c["director"],
        c["rop_north"],
        deal_id=deal["id"],
        amount="100.00",
        currency="USD",
        seller_user_id=rep_id,
    )
    _pay(
        c["director"],
        c["rop_north"],
        deal_id=deal["id"],
        amount="9000.00",
        currency="KGS",
        is_refund=True,
        seller_user_id=rep_id,
    )
    assert _ok(c["director"].get("/api/v1/payments/metrics/"))["sales_count"] == 1
    # A refund of the full amount in the same currency cancels it.
    _pay(
        c["director"],
        c["rop_north"],
        deal_id=deal["id"],
        amount="100.00",
        currency="USD",
        is_refund=True,
        seller_user_id=rep_id,
    )
    assert _ok(c["director"].get("/api/v1/payments/metrics/"))["sales_count"] == 0


def test_work_handed_to_the_director_stays_visible_to_the_department(
    company: dict[str, Any],
) -> None:
    c, users = company["c"], company["users"]
    deal = _deal(c["rep_north"], "North deal")
    task = _ok(_task(c["rep_north"], deal["id"], "Call"), 201)
    membership = Membership.objects.get(user=users["rep_north"])
    _ok(c["director"].post(f"/api/v1/staff/{membership.id}/deactivate/"))
    # The director has no department: the task keeps North, its head still sees it and the deal.
    assert c["rop_north"].get(f"/api/v1/sales/deals/{deal['id']}/").status_code == 200
    row = ActivityRecord.objects.using("tenant_acme").get(id=task["id"])
    assert row.assignee_user_id == users["director"].id
    assert str(row.team_id) == company["north"]


def test_dismissal_keeps_leads_customers_and_deals_for_manual_redistribution(
    company: dict[str, Any],
) -> None:
    c, users = company["c"], company["users"]
    rep = c["rep_north"]
    lead = _lead(rep, "Working lead", phone="+996555000201")
    customer = _ok(rep.post("/api/v1/customers/", {"name": "Active client"}, format="json"), 201)
    deal = _deal(rep, "Open deal")

    membership = Membership.objects.get(user=users["rep_north"])
    _ok(c["rop_north"].post(f"/api/v1/staff/{membership.id}/deactivate/"))
    rep_id = str(users["rep_north"].id)
    rop = c["rop_north"]
    # Nothing moves on its own: the base stays with the dismissed employee.
    assert _ok(rop.get(f"/api/v1/leads/{lead['id']}/"))["owner_user_id"] == rep_id
    assert _ok(rop.get(f"/api/v1/customers/{customer['id']}/"))["owner_user_id"] == rep_id
    assert _ok(rop.get(f"/api/v1/sales/deals/{deal['id']}/"))["owner_user_id"] == rep_id
    # The head of sales reassigns by hand; the previous owner stays in the history.
    new_owner = str(users["rop_north"].id)
    _ok(
        rop.post(f"/api/v1/leads/{lead['id']}/assign/", {"owner_user_id": new_owner}, format="json")
    )
    actions = [e["action"] for e in _ok(rop.get(f"/api/v1/leads/{lead['id']}/history/"))["results"]]
    assert "lead.reassigned" in actions


def test_payment_metrics_per_department_with_prepayments(company: dict[str, Any]) -> None:
    c = company["c"]
    north_deal = _deal(
        c["rep_north"], "North deal", customer_id=_customer(c["rep_north"], "N client")
    )
    _pay(
        c["rep_north"], c["rop_north"], deal_id=north_deal["id"], amount="100.00"
    )  # prepayment, a sale
    _pay(
        c["rep_north"], c["rop_north"], deal_id=north_deal["id"], amount="40.00", kind="additional"
    )
    south_deal = _deal(
        c["rep_south"], "South deal", customer_id=_customer(c["rep_south"], "S client")
    )
    _pay(c["rep_south"], c["rop_south"], deal_id=south_deal["id"], amount="70.00")
    _pay(
        c["rep_south"], c["rop_south"], deal_id=south_deal["id"], amount="70.00", is_refund=True
    )  # not counted

    director = c["director"]
    north = _ok(director.get(f"/api/v1/payments/metrics/?department_id={company['north']}"))
    assert (north["sales_count"], north["prepayments_count"], north["prepayments_amount"]) == (
        1,
        1,
        "100.00",
    )
    assert north["received"] == "140.00"
    south = _ok(director.get(f"/api/v1/payments/metrics/?department_id={company['south']}"))
    assert (south["sales_count"], south["prepayments_count"], south["prepayments_amount"]) == (
        0,
        1,
        "70.00",
    )
    everything = _ok(director.get("/api/v1/payments/metrics/"))
    assert everything["prepayments_count"] == 2

    # A head of sales sees their own department and is refused another one.
    assert (
        _ok(c["rop_north"].get(f"/api/v1/payments/metrics/?department_id={company['north']}"))[
            "sales_count"
        ]
        == 1
    )
    assert (
        c["rop_north"]
        .get(f"/api/v1/payments/metrics/?department_id={company['south']}")
        .status_code
        == 403
    )
    assert c["rop_north"].get("/api/v1/payments/metrics/?department_id=nope").status_code == 400
