"""Questions about data: the CRM counts with the user's rights and no model is called."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.ai_insights import composition
from modules.ai_insights.domain.agent_questions import Question, question_in
from tests.api.test_ai_agent import FakeAiPlatform, _ask, _deal, _ok
from tests.conftest import ALL_DATABASES, TenantFixture


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        ("Сколько у меня лидов?", Question.LEADS_COUNT),  # noqa: RUF001
        ("сколько открытых сделок", Question.DEALS_COUNT),
        ("Какие сделки просрочены?", Question.OVERDUE_DEALS),
        ("покажи просроченные задачи", Question.OVERDUE_TASKS),
        ("Кто выполнил план?", Question.PLAN_PROGRESS),
        ("how many leads do I have", Question.LEADS_COUNT),
        # Requests to change something are not questions, whatever they mention.
        ("Поставь задачу по сделке Поставка", None),
        ("Обнови план на ноябрь, сколько можно ждать", None),
        ("Перенеси сделку на этап Думает", None),
        ("Клиент думает", None),
    ],
)
def test_question_recognition(request_text: str, expected: Question | None) -> None:
    assert question_in(request_text) == expected


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


@pytest.mark.django_db(databases=ALL_DATABASES)
def test_counts_follow_the_users_rights_and_skip_the_model(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    rep = client_for(make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP), "acme")
    _deal(owner)
    _ok(owner.post("/api/v1/leads/", {"name": "Лид", "email": "l@example.test"}, format="json"))

    run = _ask(owner, "Сколько сделок?")
    assert (run["status"], run["provider"]) == ("succeeded", "crm")
    assert run["summary"].endswith(": 1.")
    assert _ask(rep, "Сколько сделок?")["summary"].endswith(": 0.")
    assert _ask(owner, "Сколько у меня лидов?")["summary"].endswith(": 1.")  # noqa: RUF001
    assert ai.submits == []


@pytest.mark.django_db(databases=ALL_DATABASES)
def test_overdue_deals_are_listed_with_links(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    deal_id = _deal(client)
    assert _ask(client, "Какие сделки просрочены?")["items"] == []
    _ok(
        client.patch(
            f"/api/v1/sales/deals/{deal_id}/", {"expected_close_date": "2020-01-10"}, format="json"
        )
    )
    run = _ask(client, "Какие сделки просрочены?")
    assert run["items"] == [{"title": "Внедрение", "ref": f"deal:{deal_id}"}]


@pytest.mark.django_db(databases=ALL_DATABASES)
def test_plan_progress_for_the_current_period(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    assert "нет" in _ask(client, "Как выполнен план, какие цифры?")["summary"]
    body = {
        "scope": "company",
        "metric": "monthly_sales",
        "period": f"{datetime.now(UTC):%Y-%m}",
        "target": "20",
    }
    _ok(client.put("/api/v1/plans/", body, format="json"))
    run = _ask(client, "Кто выполнил план?")
    assert [i["title"] for i in run["items"]] == ["Компания: продажи 0 из 20 (0%)"]
