"""Head-of-sales cabinet API: today board, traffic light (no-activity red), access."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
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
    cid = director.post("/api/v1/customers/", {"name": "Acme"}, format="json").json()["id"]
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
    paid = rep.post(
        "/api/v1/payments/",
        {"deal_id": deal_id, "amount": "1000.00", "kind": "prepayment", "paid_on": f"{month}-15"},
        format="json",
    )
    assert paid.status_code == 201, paid.content

    pf = director.get(f"/api/v1/team/plan-fact/?month={month}").json()
    row = next(r for r in pf["rows"] if r["username"] == "rep")
    assert row["plan_sales"] == "5.00"
    assert row["sales_actual"] == 1
    assert row["received_actual"] == "1000.00"
    assert row["remaining_sales"] == "4.00"
    assert pf["sales_actual_total"] >= 1
