"""Import of deals from CSV (requirement 20.5): the same preview → commit flow as leads and
customers, deals created through the sales services with the owner decisions intact
(no sale without a payment, lost needs a reason, a later open stage needs a next step)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.activities.infrastructure.models import ActivityRecord
from modules.audit.infrastructure.models import AuditRecord
from modules.customers.infrastructure.models import CustomerRecord
from modules.imports.domain.rows import parse_amount, parse_deal_rows
from modules.sales_pipeline.infrastructure.models import DealRecord
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)

_STAGES = [
    {"name": "Новый", "kind": "open"},
    {"name": "Квалификация", "kind": "open"},
    {"name": "Успешно", "kind": "won"},
    {"name": "Отказ", "kind": "lost"},
]
_HEADER = (
    "Сделка;Сумма;Валюта;Этап;Ответственный;Клиент;Телефон;E-mail;Дата закрытия;"
    "Источник;Следующий шаг;Срок шага;Причина проигрыша\n"
)


def _ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.content
    return response.json()


def _in_department(user: Any, tenant: TenantFixture, name: str) -> None:
    from modules.organizations.infrastructure.models import Department, Membership

    department, _ = Department.objects.get_or_create(organization=tenant.org, name=name)
    Membership.objects.filter(user=user, organization=tenant.org).update(department=department)


def _pipeline(client: APIClient) -> dict[str, str]:
    pipeline = _ok(
        client.post(
            "/api/v1/sales/pipelines/",
            {"name": "Продажи", "is_default": True, "stages": _STAGES},
            format="json",
        ),
        201,
    )
    return {s["name"]: s["id"] for s in pipeline["stages"]}


def _preview(client: APIClient, content: str, **extra: Any) -> dict[str, Any]:
    body = {"entity": "deals", "content": content, **extra}
    return _ok(client.post("/api/v1/imports/preview/", body, format="json"))  # type: ignore[no-any-return]


def _commit(client: APIClient, content: str, **extra: Any) -> dict[str, Any]:
    body = {"entity": "deals", "filename": "deals.csv", "content": content, **extra}
    return _ok(client.post("/api/v1/imports/", body, format="json"), 201)  # type: ignore[no-any-return]


def test_parse_deal_rows() -> None:
    assert parse_amount("1 200,50") == (parse_amount("1200.50")[0], None)
    assert parse_amount("-5")[1] is not None
    rows = parse_deal_rows(
        "Title;Amount;Close date;Next step\nA;10;31.12.2026;Позвонить\n;x;2026-13-01;\n"
    )
    assert rows[0].expected_close_date is not None and rows[0].expected_close_date.day == 31
    assert any("Следующий шаг" in e for e in rows[0].errors)  # a step needs its due date
    assert len(rows[1].errors) == 3  # no title, bad amount, bad date


def test_preview_then_import_deals(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    tenant = tenants["acme"]
    boss = make_user("boss", tenant=tenant, role=Role.DIRECTOR)
    rep = make_user("north_rep", tenant=tenant, role=Role.SALES_REP)
    director = client_for(boss, "acme")
    stages = _pipeline(director)
    _ok(director.post("/api/v1/sales/loss-reasons/", {"name": "Дорого"}, format="json"), 201)
    old = _ok(
        director.post(
            "/api/v1/customers/", {"name": "Старый", "phone": "+996700000001"}, format="json"
        ),
        201,
    )
    csv_text = _HEADER + (
        "Старому клиенту;1000;;;;;+996700000001;;;сайт;;;\n"  # 2: matched, first stage
        "Новому клиенту;2 500,50;usd;квалификация;NORTH_REP;ОсОО Бета;+996555000222;;"  # noqa: RUF001
        "31.12.2026;;Позвонить;2030-01-10;\n"  # 3: new customer, rep, moved with next step
        "Проданная;100;;Продажа;;;;;;;;;\n"  # 4: won stage -> refused
        "Без шага;100;;Квалификация;;;;;;;;;\n"  # 5: later open stage without next step
        "Проигранная;100;;Отказ;;;;;;;;;\n"  # 6: lost without a reason
        "Проиграна по каталогу;100;;Отказ;;;;;;;;;дорого\n"  # 7: lost, catalog reason
        "Проиграна своими словами;100;;Отказ;;;;;;;;;Ушли к конкуренту\n"  # 8: free text
        "Перенос;100;;Встреча перенесена;;;;;;;;;\n"  # 9: needs a meeting date
        "Новому клиенту;500;;;;ОсОО Бета;+996555000222;;;;;;\n"  # 10: repeats line 3  # noqa: RUF001
        "Ещё сделка Беты;300;;;;ОсОО Бета;+996555000222;;;;;;\n"  # 11: reuses line 3's customer  # noqa: RUF001
        "Чужой ответственный;1;;;nobody;;;;;;;;\n"  # 12: unknown owner
    )

    preview = _preview(director, csv_text)
    rows = {row["line"]: row for row in preview["rows"]}
    assert (preview["new"], preview["duplicates"], preview["invalid"]) == (5, 1, 5)
    assert rows[2]["deal"]["customer_action"] == "match"
    assert rows[2]["deal"]["customer_id"] == old["id"]
    assert rows[2]["deal"]["currency"] == "KGS"  # the company currency
    assert rows[3]["deal"]["customer_action"] == "create"
    assert rows[3]["deal"]["owner_user_id"] == str(rep.id)
    assert rows[3]["deal"]["stage_name"] == "Квалификация"
    assert "оплаты" in rows[4]["reason"] and rows[4]["status"] == "invalid"
    assert "следующий шаг" in rows[5]["reason"]
    assert "причину проигрыша" in rows[6]["reason"]
    assert "Встреча перенесена" in rows[9]["reason"]
    assert rows[10]["status"] == "duplicate" and rows[10]["duplicate_line"] == 3
    assert "nobody" in rows[12]["reason"]
    assert DealRecord.objects.using("tenant_acme").count() == 0  # the preview saves nothing

    job = _commit(director, csv_text)
    assert (job["created"], job["duplicates"], job["invalid"], job["failed"]) == (5, 1, 5, 0)
    assert {e["line"] for e in job["errors"]} == {4, 5, 6, 9, 12}

    deals = {d["title"]: d for d in director.get("/api/v1/sales/deals/").json()["results"]}
    assert deals["Старому клиенту"]["customer_id"] == old["id"]
    assert deals["Старому клиенту"]["stage_id"] == stages["Новый"]
    assert deals["Старому клиенту"]["owner_user_id"] == str(boss.id)
    assert deals["Старому клиенту"]["source"] == "сайт"
    moved = deals["Новому клиенту"]
    assert (moved["stage_id"], moved["owner_user_id"]) == (stages["Квалификация"], str(rep.id))
    assert (moved["amount"], moved["currency"]) == ("2500.50", "USD")
    assert moved["expected_close_date"] == "2026-12-31"
    beta = CustomerRecord.objects.using("tenant_acme").get(phone="+996555000222")
    assert beta.owner_user_id == rep.id  # the new customer goes to the deal's owner
    assert moved["customer_id"] == str(beta.id)
    assert deals["Ещё сделка Беты"]["customer_id"] == str(beta.id)  # no duplicate customer
    task = ActivityRecord.objects.using("tenant_acme").get(subject_id=moved["id"])
    assert (task.title, task.assignee_user_id) == ("Позвонить", rep.id)
    lost = deals["Проиграна по каталогу"]
    assert lost["status"] == "lost" and lost["loss_reason_id"] is not None
    assert deals["Проиграна своими словами"]["lost_reason"] == "Ушли к конкуренту"
    # Nothing becomes a sale without a payment.
    assert not DealRecord.objects.using("tenant_acme").filter(status="won").exists()
    assert (
        AuditRecord.objects.using("tenant_acme")
        .filter(action="deal.created", actor_user_id=boss.id)
        .count()
        == 5
    )

    # Importing the same file again creates nothing: every created row is now a duplicate.
    again = _commit(director, csv_text)
    assert (again["created"], again["duplicates"], again["invalid"]) == (0, 6, 5)
    assert DealRecord.objects.using("tenant_acme").count() == 5


def test_owner_rules_follow_deal_assignment(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    tenant = tenants["acme"]
    boss = make_user("boss", tenant=tenant, role=Role.DIRECTOR)
    head_user = make_user("north_head", tenant=tenant, role=Role.HEAD_OF_SALES)
    north_rep = make_user("north_rep", tenant=tenant, role=Role.SALES_REP)
    south_rep = make_user("south_rep", tenant=tenant, role=Role.SALES_REP)
    caller = make_user("caller", tenant=tenant, role=Role.LEAD_CALLER)
    for user, department in ((head_user, "North"), (north_rep, "North"), (south_rep, "South")):
        _in_department(user, tenant, department)
    _pipeline(client_for(boss, "acme"))
    head = client_for(head_user, "acme")

    content = (
        "Сделка;Ответственный\n"
        "Своему;north_rep\n"
        "Чужому отделу;south_rep@example.test\n"  # e-mail works as well as the login
    )
    preview = _preview(head, content)
    rows = {row["line"]: row for row in preview["rows"]}
    assert rows[2]["status"] == "new"
    assert rows[3]["status"] == "invalid" and "своего отдела" in rows[3]["reason"]
    job = _commit(head, content)
    assert (job["created"], job["invalid"]) == (1, 1)
    deal = DealRecord.objects.using("tenant_acme").get(title="Своему")
    assert deal.owner_user_id == north_rep.id

    # The request-wide owner is checked the same way.
    outside = _commit(head, "Сделка\nЮгу\n", owner_user_id=str(south_rep.id))  # noqa: RUF001
    assert (outside["created"], outside["invalid"]) == (0, 1)

    # A manager imports deals only for themselves (deals.create with own scope).
    manager = client_for(north_rep, "acme")
    own = _commit(manager, "Сделка;Ответственный\nМоя;\nДругому;north_head\n")  # noqa: RUF001
    assert (own["created"], own["invalid"]) == (1, 1)
    assert "только себе" in own["errors"][0]["message"]  # noqa: RUF001
    assert DealRecord.objects.using("tenant_acme").get(title="Моя").owner_user_id == north_rep.id
    # A manager links a deal only to a customer they can see.
    _ok(
        client_for(boss, "acme").post(
            "/api/v1/customers/", {"name": "Чужой", "phone": "+996700000009"}, format="json"
        ),
        201,
    )
    hidden = _preview(manager, "Сделка;Клиент;Телефон\nЧужая;Чужой;+996700000009\n")  # noqa: RUF001
    assert "закреплён за другим сотрудником" in hidden["rows"][0]["reason"]

    # Without deals.create the import is refused, as for leads and customers.
    response = client_for(caller, "acme").post(
        "/api/v1/imports/preview/",
        {"entity": "deals", "content": "Сделка\nБыстрая\n"},  # noqa: RUF001
        format="json",
    )
    assert response.status_code == 403


def test_deal_import_is_tenant_isolated(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    acme = client_for(make_user("acme_boss", tenant=tenants["acme"], role=Role.DIRECTOR), "acme")
    globex = client_for(
        make_user("globex_boss", tenant=tenants["globex"], role=Role.DIRECTOR), "globex"
    )
    _pipeline(acme)
    _ok(
        acme.post("/api/v1/customers/", {"name": "Акме", "phone": "+996700000001"}, format="json"),
        201,
    )
    _commit(acme, "Сделка;Клиент;Телефон\nОбщая;Акме;+996700000001\n")  # noqa: RUF001

    # Globex has no pipeline yet, and acme's customer and deal are invisible to it.
    no_pipeline = _preview(globex, "Сделка;Клиент;Телефон\nОбщая;Гло;+996700000001\n")  # noqa: RUF001
    assert "нет воронки" in no_pipeline["rows"][0]["reason"]
    _pipeline(globex)
    preview = _preview(globex, "Сделка;Клиент;Телефон\nОбщая;Гло;+996700000001\n")  # noqa: RUF001
    assert preview["rows"][0]["status"] == "new"
    assert preview["rows"][0]["deal"]["customer_action"] == "create"
    assert _commit(globex, "Сделка;Клиент;Телефон\nОбщая;Гло;+996700000001\n")["created"] == 1  # noqa: RUF001
    assert DealRecord.objects.using("tenant_acme").count() == 1
    assert DealRecord.objects.using("tenant_globex").count() == 1
    assert globex.get("/api/v1/imports/").json()["count"] == 1
