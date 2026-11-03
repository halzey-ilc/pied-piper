"""Cabinet analytics: funnel conversion and the bottleneck, losses by reason, meeting
cancel/no-show reasons, lead first response, day-score history — and who may see them."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)

_STAGES = [
    {"name": "Новый", "kind": "open"},
    {"name": "Тёплый", "kind": "open"},
    {"name": "Думает", "kind": "open"},
    {"name": "Успешная", "kind": "won"},
    {"name": "Отказ", "kind": "lost"},
]


def _in_department(user: Any, tenant: TenantFixture, name: str) -> str:
    from modules.organizations.infrastructure.models import Department, Membership

    department, _ = Department.objects.get_or_create(organization=tenant.org, name=name)
    Membership.objects.filter(user=user, organization=tenant.org).update(department=department)
    return str(department.id)


def _window() -> str:
    """A period around today wide enough for any company timezone."""
    today = datetime.now(UTC).date()
    return f"date_from={today - timedelta(days=2)}&date_to={today + timedelta(days=1)}"


def _move(client: APIClient, deal_id: str, stage_id: str, **extra: Any) -> None:
    moved = client.post(
        f"/api/v1/sales/deals/{deal_id}/stage/", {"stage_id": stage_id, **extra}, format="json"
    )
    assert moved.status_code == 200, moved.content


def test_funnel_conversion_bottleneck_and_losses(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    director = client_for(make_user("boss", tenant=tenants["acme"], role=Role.DIRECTOR), "acme")
    pipeline = director.post(
        "/api/v1/sales/pipelines/",
        {"name": "Продажи", "is_default": True, "stages": _STAGES},
        format="json",
    ).json()
    stage = {s["name"]: s["id"] for s in pipeline["stages"]}
    deals = [
        director.post(
            "/api/v1/sales/deals/", {"title": f"D{i}", "amount": "100.00"}, format="json"
        ).json()["id"]
        for i in range(4)
    ]
    for deal_id in deals[:3]:
        _move(director, deal_id, stage["Тёплый"])
    _move(director, deals[0], stage["Успешная"])
    reason_id = director.post(
        "/api/v1/sales/loss-reasons/", {"name": "Дорого"}, format="json"
    ).json()["id"]
    _move(director, deals[3], stage["Отказ"], loss_reason_id=reason_id)

    response = director.get(f"/api/v1/team/funnel/?{_window()}")
    assert response.status_code == 200, response.content
    funnel = response.json()
    assert funnel["pipeline_id"] == pipeline["id"]
    rows = {s["name"]: s for s in funnel["stages"]}
    assert rows["Новый"]["reached"] == 4 and rows["Новый"]["moved_on"] == 3
    assert float(rows["Новый"]["conversion"]) == pytest.approx(0.75)
    assert rows["Тёплый"]["reached"] == 3 and rows["Тёплый"]["moved_on"] == 1
    assert rows["Тёплый"]["open_deals"] == 2 and rows["Тёплый"]["open_amount"] == "200.00"
    assert rows["Тёплый"]["is_bottleneck"] is True
    assert rows["Думает"]["reached"] == 0 and rows["Думает"]["conversion"] is None
    assert rows["Тёплый"]["stale_deals"] == 0 and rows["Тёплый"]["median_age_days"] == "0.0"
    assert funnel["won"] == 1 and funnel["lost"] == 1
    assert funnel["won_previous"] == 0 and funnel["stale_days"] == 14
    assert director.get("/api/v1/team/funnel/?stale_days=0").status_code == 400

    # Meetings: one cancelled with a reason, one no-show with a list reason.
    start = datetime.now(UTC) - timedelta(hours=2)
    meetings = [
        director.post(
            "/api/v1/meetings/",
            {
                "title": f"M{i}",
                "scheduled_start": start.isoformat(),
                "scheduled_end": (start + timedelta(minutes=30)).isoformat(),
            },
            format="json",
        ).json()["id"]
        for i in range(3)
    ]
    cancelled = director.post(
        f"/api/v1/meetings/{meetings[0]}/cancel/", {"reason": "Клиент заболел"}, format="json"
    )
    assert cancelled.status_code == 200, cancelled.content
    no_show = director.post(
        f"/api/v1/meetings/{meetings[1]}/outcome/",
        {"outcome": "no_show", "reason": "Без звонка", "comment": "ждали 20 минут"},
        format="json",
    )
    assert no_show.status_code == 200, no_show.content
    assert no_show.json()["outcome_reason"] == "Без звонка"

    # Leads: two new, one handled.
    leads = [
        director.post(
            "/api/v1/leads/",
            {"name": f"Lead {i}", "email": f"lead{i}@example.test"},
            format="json",
        ).json()["id"]
        for i in range(2)
    ]
    qualified = director.post(f"/api/v1/leads/{leads[0]}/qualify/", {}, format="json")
    assert qualified.status_code == 200, qualified.content

    losses = director.get(f"/api/v1/team/losses/?{_window()}")
    assert losses.status_code == 200, losses.content
    body = losses.json()
    assert body["lost_deals"] == 1 and body["lost_previous"] == 0
    assert [r["reason"] for r in body["loss_reasons"]] == ["Дорого"]
    assert sum(body["loss_reasons"][0]["by_month"].values()) == 1
    assert body["meetings_total"] == 3
    assert body["meetings_cancelled"] == 1 and body["meetings_no_show"] == 1
    assert body["cancel_reasons"][0]["reason"] == "Клиент заболел"
    assert body["no_show_reasons"][0]["reason"] == "Без звонка"
    assert body["lead_response"]["leads"] == 2
    assert body["lead_response"]["responded"] == 1
    assert body["lead_response"]["within_15_min"] == 1


def test_funnel_and_losses_scope(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    tenant = tenants["acme"]
    director = client_for(make_user("boss", tenant=tenant, role=Role.DIRECTOR), "acme")
    head_user = make_user("north_head", tenant=tenant, role=Role.HEAD_OF_SALES)
    rep_user = make_user("north_rep", tenant=tenant, role=Role.SALES_REP)
    _in_department(head_user, tenant, "North")
    _in_department(rep_user, tenant, "North")
    head, rep = client_for(head_user, "acme"), client_for(rep_user, "acme")
    director.post(
        "/api/v1/sales/pipelines/",
        {"name": "Продажи", "is_default": True, "stages": _STAGES},
        format="json",
    )
    director.post("/api/v1/sales/deals/", {"title": "Boss", "amount": "1.00"}, format="json")
    created = rep.post("/api/v1/sales/deals/", {"title": "Rep", "amount": "1.00"}, format="json")
    assert created.status_code == 201, created.content

    company = director.get(f"/api/v1/team/funnel/?{_window()}").json()
    department = head.get(f"/api/v1/team/funnel/?{_window()}").json()
    assert company["stages"][0]["reached"] == 2
    # A head of sales sees only their department's deals.
    assert department["stages"][0]["reached"] == 1
    assert rep.get("/api/v1/team/funnel/").status_code == 403
    assert rep.get("/api/v1/team/losses/").status_code == 403
    assert head.get("/api/v1/team/losses/").status_code == 200


def test_score_history(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    tenant = tenants["acme"]
    director = client_for(make_user("boss", tenant=tenant, role=Role.DIRECTOR), "acme")
    rep_user = make_user("rep1", tenant=tenant, role=Role.SALES_REP)
    other_head = make_user("south_head", tenant=tenant, role=Role.HEAD_OF_SALES)
    _in_department(rep_user, tenant, "North")
    _in_department(other_head, tenant, "South")
    rep = client_for(rep_user, "acme")
    cid = rep.post("/api/v1/customers/", {"name": "Acme"}, format="json").json()["id"]
    call = rep.post(
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

    response = director.get(f"/api/v1/team/members/{rep_user.id}/scores/")
    assert response.status_code == 200, response.content
    points = response.json()["points"]
    assert len(points) == 30
    assert all(p["reason"] in (None, "silent", "absent") for p in points)
    assert points[0]["reason"] == "silent" and points[0]["points"] is None
    # Today the rep called: the day is graded with the board's rule.
    assert points[-1]["points"] is not None and points[-1]["grade"] in (2, 3, 4, 5)

    url = f"/api/v1/team/members/{rep_user.id}/scores/"
    assert client_for(other_head, "acme").get(url).status_code == 404
    assert rep.get(url).status_code == 403
    assert director.get(f"{url}?date_from=2026-01-01&date_to=2026-12-31").status_code == 400
