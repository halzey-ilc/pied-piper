"""Head-of-sales cabinet API: today board, traffic light (no-activity red), access."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from tests.api.payment_helpers import pay_confirmed
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)


def test_today_board_traffic_light_and_inactivity(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    director = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    rep1_user = make_user("rep1", tenant=tenants["acme"], role=Role.SALES_REP)
    rep1 = client_for(rep1_user, "acme")
    make_user("rep2", tenant=tenants["acme"], role=Role.SALES_REP)  # a member with no activity

    # rep1 has activity today (a call); rep2 has none.
    cid = rep1.post("/api/v1/customers/", {"name": "Acme"}, format="json").json()["id"]
    call = rep1.post(
        "/api/v1/activities/",
        {
            "kind": "call",
            "subject_type": "customer",
            "subject_id": cid,
            "direction": "outbound",
            "occurred_at": datetime.now(UTC).isoformat(),
            "duration_seconds": 60,
        },
        format="json",
    )
    assert call.status_code == 201, call.content

    board = director.get("/api/v1/team/today/")
    assert board.status_code == 200, board.content
    data = board.json()
    rows = {r["username"]: r for r in data["managers"]}
    assert rows["rep1"]["calls"] == 1 and rows["rep1"]["has_activity"] is True
    assert rows["rep1"]["status"] == "green"
    assert rows["rep2"]["status"] == "red"
    assert "no_activity" in rows["rep2"]["red_reasons"]
    assert data["totals"]["managers"] >= 3
    assert data["totals"]["red"] >= 1

    inactivity = director.get("/api/v1/team/inactivity/").json()
    names = {m["username"] for m in inactivity["managers"]}
    assert "rep2" in names and "rep1" not in names

    # A sales rep cannot open the cabinet.
    assert rep1.get("/api/v1/team/today/").status_code == 403


def test_absence_suppresses_no_activity_red(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    director = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    away_user = make_user("away", tenant=tenants["acme"], role=Role.SALES_REP)

    # A window around today, so it covers "today" in the company timezone regardless
    # of where the UTC date boundary falls.
    today = datetime.now(UTC).date()
    created = director.post(
        "/api/v1/absences/",
        {
            "user_id": str(away_user.id),
            "kind": "vacation",
            "start_date": (today - timedelta(days=1)).isoformat(),
            "end_date": (today + timedelta(days=1)).isoformat(),
        },
        format="json",
    )
    assert created.status_code == 201, created.content

    rows = {r["username"]: r for r in director.get("/api/v1/team/today/").json()["managers"]}
    # On absence today: not flagged red for "no activity".
    assert rows["away"]["on_absence"] is True
    assert "no_activity" not in rows["away"]["red_reasons"]


def test_plan_fact_plan_versus_actual(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    director = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    rep_user = make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP)
    rep = client_for(rep_user, "acme")

    # An explicit month keeps the test independent of the company-timezone boundary.
    today = datetime.now(UTC).date()
    month = f"{today.year:04d}-{today.month:02d}"

    # Director sets the rep's monthly sales plan.
    plan = director.put(
        "/api/v1/plans/",
        {
            "scope": "user",
            "scope_id": str(rep_user.id),
            "metric": "monthly_sales",
            "period": month,
            "target": "5",
        },
        format="json",
    )
    assert plan.status_code == 200, plan.content

    # The rep takes the first payment on a deal this month.
    director.post(
        "/api/v1/sales/pipelines/",
        {"name": "P", "is_default": True, "stages": [{"name": "New"}]},
        format="json",
    )
    deal_id = director.post(
        "/api/v1/sales/deals/", {"title": "D", "amount": "3000"}, format="json"
    ).json()["id"]
    pay_confirmed(
        rep, director, deal_id=deal_id, amount="1000.00", kind="prepayment", paid_on=f"{month}-15"
    )
    # A payment in another currency is never added to the company-currency money figures.
    pay_confirmed(
        rep,
        director,
        deal_id=deal_id,
        amount="50.00",
        currency="EUR",
        kind="additional",
        paid_on=f"{month}-16",
    )

    pf = director.get(f"/api/v1/team/plan-fact/?month={month}").json()
    assert len(pf["currency"]) == 3 and pf["currency"] != "EUR"
    row = next(r for r in pf["rows"] if r["username"] == "rep")
    assert row["plan_sales"] == "5.00"
    assert row["sales_actual"] == 1
    assert row["received_actual"] == "1000.00"
    assert row["remaining_sales"] == "4.00"
    assert pf["sales_actual_total"] >= 1


def _customer_deal(client: APIClient, title: str = "Deal") -> tuple[str, str]:
    cid = client.post("/api/v1/customers/", {"name": f"C {title}"}, format="json").json()["id"]
    if client.get("/api/v1/sales/pipelines/").json() == []:
        client.post(
            "/api/v1/sales/pipelines/",
            {"name": "P", "is_default": True, "stages": [{"name": "New", "kind": "open"}]},
            format="json",
        )
    deal = client.post(
        "/api/v1/sales/deals/",
        {"title": title, "amount": "100.00", "customer_id": cid},
        format="json",
    )
    assert deal.status_code == 201, deal.content
    return cid, deal.json()["id"]


def test_today_board_extended_fields_and_day_score(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    director = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    rep_user = make_user("rep1", tenant=tenants["acme"], role=Role.SALES_REP)
    rep = client_for(rep_user, "acme")
    cid = rep.post("/api/v1/customers/", {"name": "Acme"}, format="json").json()["id"]
    later = datetime.now(UTC) + timedelta(minutes=30)
    meeting = rep.post(
        "/api/v1/meetings/",
        {
            "title": "Upcoming",
            "scheduled_start": later.isoformat(),
            "scheduled_end": (later + timedelta(minutes=30)).isoformat(),
            "subject_type": "customer",
            "subject_id": cid,
        },
        format="json",
    )
    assert meeting.status_code == 201, meeting.content
    rows = {r["username"]: r for r in director.get("/api/v1/team/today/").json()["managers"]}
    row = rows["rep1"]
    # The meeting may fall on tomorrow near midnight; only check consistency then.
    if row["meetings_total"]:
        assert row["meetings_to_confirm"] == 1
        assert row["next_to_confirm_at"] is not None
    assert row["meetings_cancelled"] == 0
    score = row["score"]
    if row["has_activity"]:
        assert score["plan_missing"] is True
        assert 0 <= score["points"] <= 100 and score["grade"] in (2, 3, 4, 5)
    else:
        assert score is None  # nothing to grade on a silent day


def test_plan_fact_department_plan_member_card_and_reports(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    director = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    dept = director.post("/api/v1/departments/", {"name": "North"}, format="json").json()["id"]
    created = director.post(
        "/api/v1/staff/",
        {"first_name": "Head", "role": "head_of_sales", "login": "head.n", "department_id": dept},
        format="json",
    )
    assert created.status_code == 201, created.content
    rep_user = make_user("rep1", tenant=tenants["acme"], role=Role.SALES_REP)
    rep = client_for(rep_user, "acme")
    month = datetime.now(UTC).strftime("%Y-%m")
    put = director.put(
        "/api/v1/plans/",
        {
            "scope": "department",
            "scope_id": dept,
            "metric": "monthly_sales",
            "period": month,
            "target": "10",
        },
        format="json",
    )
    assert put.status_code == 200, put.content
    pf = director.get(f"/api/v1/team/plan-fact/?department_id={dept}").json()
    assert pf["department_plan_sales"] == "10.00"
    assert pf["department_remaining"] is not None and pf["department_percent"] == "0.0"

    # The director sees any member's card; an unknown member is 404.
    card = director.get(f"/api/v1/team/members/{rep_user.id}/card/")
    assert card.status_code == 200, card.content
    body = card.json()
    assert len(body["activity"]) == 14
    assert set(body["current"]) >= {"meetings_held", "sales", "attendance", "meeting_to_sale"}
    assert "department_average" in body and "previous" in body
    assert director.get(f"/api/v1/team/members/{dept}/card/").status_code == 404
    assert rep.get(f"/api/v1/team/members/{rep_user.id}/card/").status_code == 403

    report = director.get("/api/v1/team/reports/?period=week").json()
    assert report["period"] == "week"
    assert any(r["username"] == "rep1" for r in report["rows"])
    csv_response = director.get("/api/v1/team/reports/?period=month&export=csv")
    assert csv_response.status_code == 200
    assert csv_response["Content-Type"].startswith("text/csv")
    assert csv_response.content.decode().startswith("username,")


def test_review_task_type_and_filter(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    _cid, deal = _customer_deal(owner)
    review = owner.post(
        "/api/v1/activities/",
        {
            "kind": "task",
            "subject_type": "deal",
            "subject_id": deal,
            "title": "Re-call",
            "task_type": "review",
        },
        format="json",
    )
    assert review.status_code == 201, review.content
    owner.post(
        "/api/v1/activities/",
        {"kind": "task", "subject_type": "deal", "subject_id": deal, "title": "Other"},
        format="json",
    )
    found = owner.get("/api/v1/activities/?kind=task&task_type=review").json()
    assert [a["title"] for a in found["results"]] == ["Re-call"]
