# ruff: noqa: RUF001
"""Value-changing requests: the CRM reads the value, shows it for confirmation and the
owning module applies it. The model is never involved."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.ai_insights import composition
from modules.ai_insights.domain.agent_commands import CommandKind, command_in
from tests.api.test_ai_agent import FakeAiPlatform, _ask, _deal, _ok
from tests.conftest import ALL_DATABASES, TenantFixture


@pytest.mark.parametrize(
    ("text", "kind", "number"),
    [
        ("Измени сумму сделки Поставка на 500 000", CommandKind.DEAL_AMOUNT, Decimal(500000)),
        ("поставь сумму по сделке Тест до 1,5 млн", CommandKind.DEAL_AMOUNT, Decimal(1500000)),
        ("Обнови план продаж на ноябрь до 30", CommandKind.PLAN_TARGET, Decimal(30)),
        ("Смени ответственного по сделке Тест на Иванова", CommandKind.DEAL_OWNER, None),
        ("Передай сделку Тест Айгерим", CommandKind.DEAL_OWNER, None),
        ("Встреча по сделке Тест состоялась: клиент доволен", CommandKind.MEETING_OUTCOME, None),
    ],
)
def test_command_recognition(text: str, kind: CommandKind, number: Decimal | None) -> None:
    command = command_in(text)
    assert command is not None and command.kind is kind and command.number == number


@pytest.mark.parametrize(
    "text",
    [
        "Поставь задачу по сделке Поставка завтра",
        "Запиши заметку: встреча прошла хорошо",
        "Назначь встречу по сделке Тест во вторник",
        "Какая сумма у сделки?",
        "Клиент думает",
    ],
)
def test_other_requests_stay_with_the_planner(text: str) -> None:
    assert command_in(text) is None


def test_plan_month_and_metric_are_read() -> None:
    command = command_in("Обнови план оборота на март до 2 млн")
    assert command is not None
    assert (command.metric, command.month, command.number) == (
        "monthly_turnover",
        3,
        Decimal(2000000),
    )


@pytest.fixture
def ai(settings: Any, monkeypatch: pytest.MonkeyPatch) -> FakeAiPlatform:
    settings.AI_PLATFORM = {
        **settings.AI_PLATFORM,
        "ENABLED": True,
        "URL": "http://ai.test",
        "TOKEN_KEY": "api-test-ai-platform-key-0123456789abcdef",
    }
    fake = FakeAiPlatform()
    monkeypatch.setattr(composition, "ai_platform_client", lambda: fake)
    return fake


def _confirm(client: APIClient, run: dict[str, Any]) -> dict[str, Any]:
    done: dict[str, Any] = _ok(client.post(f"/api/v1/ai/agent/runs/{run['id']}/actions/0/confirm/"))
    return done


@pytest.mark.django_db(databases=ALL_DATABASES)
def test_amount_changes_only_after_confirmation_and_shows_before_and_after(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    deal_id = _deal(client)
    run = _ask(client, "Измени сумму сделки Внедрение на 500 000")
    assert run["status"] == "succeeded" and run["provider"] == "crm"
    action = run["actions"][0]
    assert action["tool"] == "update_deal_amount" and action["status"] == "proposed"
    assert action["args"]["change"]["before"].startswith("10")
    assert action["args"]["change"]["after"].startswith("500 000")
    assert float(client.get(f"/api/v1/sales/deals/{deal_id}/").json()["amount"]) == 10

    assert _confirm(client, run)["status"] == "executed"
    assert float(client.get(f"/api/v1/sales/deals/{deal_id}/").json()["amount"]) == 500000
    assert ai.submits == []


@pytest.mark.django_db(databases=ALL_DATABASES)
def test_owner_is_matched_by_name_and_changed_on_confirmation(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    rep = make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP)
    rep.first_name, rep.last_name = "Айгерим", "Садыкова"
    rep.save()
    client = client_for(owner, "acme")
    deal_id = _deal(client)

    assert "Не нашёл" in _ask(client, "Передай сделку Внедрение Бакыту")["summary"]
    run = _ask(client, "Передай сделку Внедрение Садыковой")
    assert run["actions"][0]["args"]["change"]["after"] == "Айгерим Садыкова"
    assert _confirm(client, run)["status"] == "executed"
    assert client.get(f"/api/v1/sales/deals/{deal_id}/").json()["owner_user_id"] == str(rep.pk)


@pytest.mark.django_db(databases=ALL_DATABASES)
def test_plan_is_changed_only_by_someone_who_may(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    director = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    rep = client_for(make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP), "acme")
    assert "Такого плана" in _ask(director, "Обнови план продаж до 30")["summary"]
    period = f"{datetime.now(UTC):%Y-%m}"
    body = {"scope": "company", "metric": "monthly_sales", "period": period, "target": "20"}
    _ok(director.put("/api/v1/plans/", body, format="json"))

    run = _ask(director, "Обнови план продаж до 30")
    change = run["actions"][0]["args"]["change"]
    assert (change["before"], change["after"]) == ("20", "30")
    assert _confirm(director, run)["status"] == "executed"
    plans = director.get(f"/api/v1/plans/?period={period}").json()["results"]
    assert float(plans[0]["target"]) == 30

    refused = _ask(rep, "Обнови план продаж до 40")
    assert refused["actions"] == []


@pytest.mark.django_db(databases=ALL_DATABASES)
def test_meeting_outcome_goes_through_the_meetings_module(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    deal_id = _deal(client)
    assert "нет назначенной" in _ask(client, "Встреча по сделке Внедрение отменена")["summary"]
    start = datetime.now(UTC) + timedelta(days=1)
    meeting = _ok(
        client.post(
            "/api/v1/meetings/",
            {
                "title": "Демо",
                "scheduled_start": start.isoformat(),
                "scheduled_end": (start + timedelta(hours=1)).isoformat(),
                "subject_type": "deal",
                "subject_id": deal_id,
            },
            format="json",
        )
    )
    run = _ask(client, "Встреча по сделке Внедрение отменена: клиент заболел")
    assert run["actions"][0]["args"]["change"]["after"] == "Отменена"
    assert _confirm(client, run)["status"] == "executed"
    assert client.get(f"/api/v1/meetings/{meeting['id']}/").json()["status"] == "cancelled"
