"""«Мой день» of a sales manager (owner decision of 05.11.2026): the product catalog on deals,
the director's pay terms, «Мой месяц» (sales, money, meetings, earnings, products) and the
manager's own «Фокус» with tasks on mistakes and debts to collect."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.activities.infrastructure.models import ActivityRecord
from modules.sales_pipeline.infrastructure.models import DealRecord
from platform_core.application.context import tenant_scope
from tests.api.test_team_today_actions import _World
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)

_STAGES = [
    {"name": "Новый", "probability": 10, "kind": "open"},
    {"name": "Выиграна", "probability": 100, "kind": "won"},
    {"name": "Проиграна", "probability": 0, "kind": "lost"},
]


def _product(client: APIClient, name: str, **extra: Any) -> dict[str, Any]:
    response = client.post("/api/v1/sales/products/", {"name": name, **extra}, format="json")
    assert response.status_code == 201, response.content
    body: dict[str, Any] = response.json()
    return body


def test_product_catalog_and_deal_product(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    acme = tenants["acme"]
    director = client_for(make_user("boss", tenant=acme, role=Role.DIRECTOR), "acme")
    head = client_for(make_user("head", tenant=acme, role=Role.HEAD_OF_SALES), "acme")
    rep = client_for(make_user("rep", tenant=acme, role=Role.SALES_REP), "acme")
    pipeline = director.post(
        "/api/v1/sales/pipelines/",
        {"name": "Main", "is_default": True, "stages": _STAGES},
        format="json",
    )
    assert pipeline.status_code == 201, pipeline.content

    poland = _product(director, "  Польша ")
    assert poland["name"] == "Польша" and poland["show_on_my_day"] is True
    bulgaria = _product(head, "Болгария", show_on_my_day=False)
    assert (
        director.post("/api/v1/sales/products/", {"name": "Польша"}, format="json").status_code
        == 409
    )
    assert rep.post("/api/v1/sales/products/", {"name": "SMM"}, format="json").status_code == 403
    assert rep.get("/api/v1/sales/products/").json()["count"] == 2

    shown = director.patch(
        f"/api/v1/sales/products/{bulgaria['id']}/", {"show_on_my_day": True}, format="json"
    )
    assert shown.status_code == 200 and shown.json()["show_on_my_day"] is True

    deal = rep.post(
        "/api/v1/sales/deals/",
        {"title": "Поездка", "amount": "1000", "product_id": poland["id"]},
        format="json",
    )
    assert deal.status_code == 201, deal.content
    assert deal.json()["product_id"] == poland["id"]
    moved = rep.patch(
        f"/api/v1/sales/deals/{deal.json()['id']}/",
        {"product_id": bulgaria["id"]},
        format="json",
    )
    assert moved.status_code == 200 and moved.json()["product_id"] == bulgaria["id"]

    director.patch(f"/api/v1/sales/products/{poland['id']}/", {"is_active": False}, format="json")
    assert rep.get("/api/v1/sales/products/?active=true").json()["count"] == 1
    rejected = rep.post(
        "/api/v1/sales/deals/",
        {"title": "Ещё", "amount": "10", "product_id": poland["id"]},
        format="json",
    )
    assert rejected.status_code == 400


def test_pay_terms_are_set_by_the_director_only(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    acme = tenants["acme"]
    director = client_for(make_user("boss", tenant=acme, role=Role.DIRECTOR), "acme")
    head = client_for(make_user("head", tenant=acme, role=Role.HEAD_OF_SALES), "acme")
    rep_user = make_user("rep", tenant=acme, role=Role.SALES_REP)
    url = f"/api/v1/plans/compensation/{rep_user.id}/"
    terms = {"base_salary": "25000", "commission_percent": "5"}

    assert head.put(url, terms, format="json").status_code == 403
    assert client_for(rep_user, "acme").get("/api/v1/plans/compensation/").status_code == 403
    too_much = director.put(url, {**terms, "commission_percent": "120"}, format="json")
    assert too_much.status_code == 400
    stranger = director.put(f"/api/v1/plans/compensation/{uuid.uuid4()}/", terms, format="json")
    assert stranger.status_code == 404

    saved = director.put(url, terms, format="json")
    assert saved.status_code == 200, saved.content
    assert saved.json()["commission_percent"] == "5.00"
    updated = director.put(url, {**terms, "commission_percent": "7.5"}, format="json")
    assert updated.json()["commission_percent"] == "7.50"
    listed = director.get("/api/v1/plans/compensation/").json()
    assert listed["count"] == 1 and listed["results"][0]["user_id"] == str(rep_user.id)


def test_my_month_sales_money_meetings_earnings_and_products(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    acme = tenants["acme"]
    director = client_for(make_user("boss", tenant=acme, role=Role.DIRECTOR), "acme")
    rep_user = make_user("rep", tenant=acme, role=Role.SALES_REP)
    other = make_user("other", tenant=acme, role=Role.SALES_REP)
    rep = client_for(rep_user, "acme")
    poland = _product(director, "Польша")
    hidden = _product(director, "Скрытый", show_on_my_day=False)

    w = _World(acme)
    with tenant_scope(w.context):
        trip = w.deal(rep_user, "Польша: Айдана", amount="1000")
        DealRecord.objects.using(w.alias).filter(id=trip).update(product_id=poland["id"])
        w.payment(rep_user, "300", deal_id=trip)
        w.payment(rep_user, "200", kind="additional", deal_id=trip)
        w.payment(rep_user, "100", kind="additional", deal_id=trip, status="pending_verification")
        course = w.deal(rep_user, "Без продукта", amount="500")
        DealRecord.objects.using(w.alias).filter(id=course).update(product_id=hidden["id"])
        w.payment(rep_user, "500", kind="full", deal_id=course)
        w.payment(other, "900", kind="full", deal_id=w.deal(other, "Чужая", amount="900"))

        w.meeting(rep_user, "Состоялась", start=w.now - timedelta(minutes=50), status="held")
        w.meeting(rep_user, "Без итога", start=w.now - timedelta(minutes=30))
        w.meeting(rep_user, "Впереди", start=w.now + timedelta(minutes=30))
        w.meeting(rep_user, "Отменена", start=w.now - timedelta(minutes=40), status="cancelled")

    empty = rep.get("/api/v1/team/my-month/")
    assert empty.status_code == 200, empty.content
    assert empty.json()["earnings"] is None

    director.put(
        f"/api/v1/plans/compensation/{rep_user.id}/",
        {"base_salary": "20000", "commission_percent": "5"},
        format="json",
    )
    body = rep.get("/api/v1/team/my-month/").json()
    assert body["sales"] == 2 and body["previous_sales"] == 0
    assert body["turnover"] == "1500.00" and body["debt"] == "500.00"
    assert body["average_check"] == "750.00"
    assert body["money"] == {
        "prepayments": "300.00",
        "additional": "200.00",
        "full": "500.00",
        "refunds": "0",
        "received": "1000.00",
    }
    assert body["meetings"] == {
        "assigned": 3,
        "passed": 2,
        "ahead": 1,
        "held": 1,
        "attendance_percent": "50.0",
        "sales_per_meeting_percent": "200.0",
    }
    assert body["earnings"] == {
        "base_salary": "20000.00",
        "commission_percent": "5.00",
        "commission": "50.00",
        "total": "20050.00",
    }
    assert [p["name"] for p in body["products"]] == ["Польша"]
    product = body["products"][0]
    assert product["sales"] == 1 and product["turnover"] == "1000.00"
    assert product["money"]["prepayments"] == "300.00"
    assert product["money"]["additional"] == "200.00"

    assert rep.get("/api/v1/team/my-month/?month=2026-13").status_code == 400
    past = rep.get("/api/v1/team/my-month/?month=2020-01").json()
    assert past["sales"] == 0 and past["meetings"]["assigned"] == 0


def test_my_focus_puts_miss_tasks_first_and_lists_debts(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    acme = tenants["acme"]
    rep_user = make_user("rep", tenant=acme, role=Role.SALES_REP)
    other = make_user("other", tenant=acme, role=Role.SALES_REP)
    rep = client_for(rep_user, "acme")

    w = _World(acme)
    today = w.now.date()
    with tenant_scope(w.context):
        miss = w.task(rep_user, "Разобрать звонок: не назначил дату", today - timedelta(days=2))
        ActivityRecord.objects.using(w.alias).filter(id=miss).update(task_type="review")
        overdue = w.task(rep_user, "Перезвонить", today - timedelta(days=1))
        w.task(other, "Чужая", today - timedelta(days=1))
        won = w.deal(rep_user, "Болгария: долг", amount="1000", status="won")
        w.payment(rep_user, "400", deal_id=won)
        paid_off = w.deal(rep_user, "Оплачена", amount="300", status="won")
        w.payment(rep_user, "300", kind="full", deal_id=paid_off)
        w.deal(other, "Чужой долг", amount="1000", status="won")

    response = rep.get("/api/v1/team/my-focus/?actions_limit=20")
    assert response.status_code == 200, response.content
    groups = response.json()["groups"]
    assert [g["kind"] for g in groups] == [
        "miss_tasks",
        "overdue_tasks",
        "deals_to_push",
        "debts_to_collect",
        "leads_without_contact",
        "meetings_without_outcome",
        "deals_without_next_step",
    ]
    by_kind = {g["kind"]: g for g in groups}
    assert [i["id"] for i in by_kind["miss_tasks"]["items"]] == [str(miss)]
    assert by_kind["miss_tasks"]["items"][0]["age_days"] == 2
    assert [i["id"] for i in by_kind["overdue_tasks"]["items"]] == [str(overdue)]
    debts = by_kind["debts_to_collect"]
    assert [i["id"] for i in debts["items"]] == [str(won)]
    assert debts["items"][0]["debt"] == "600.00"
    assert debts["amounts"] == [{"currency": "KGS", "total": "600.00"}]
