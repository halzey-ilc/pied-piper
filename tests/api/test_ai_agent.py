"""CRM assistant (Agent Runtime): the AI proposes, the user confirms, the owning
module executes with the user's rights. Nothing runs without confirmation.

The AI Platform is replaced by a fake that returns a fixed plan.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.activities.infrastructure.models import ActivityRecord
from modules.ai_insights import composition
from modules.ai_insights.application.ports import AiJobState
from modules.ai_insights.infrastructure.models import AgentRunRecord
from modules.audit.infrastructure.models import AuditRecord
from platform_core.infrastructure.tenancy.services import system_tenant_scope
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)
ALIAS = "tenant_acme"


class FakeAiPlatform:
    def __init__(self) -> None:
        self.submits: list[dict[str, Any]] = []
        self.actions: list[dict[str, Any]] = []

    def submit(self, **kw: Any) -> AiJobState:
        self.submits.append(kw)
        return AiJobState(job_id="job-1", status="queued")

    def fetch(self, **kw: Any) -> AiJobState:
        return AiJobState(
            job_id=kw["job_id"],
            status="succeeded",
            schema_id="agent_plan.output.v1",
            provider="mock",
            model="m",
            output={
                "schema": "agent_plan.output.v1",
                "summary": "s",
                "actions": self.actions,
                "insufficient_data": not self.actions,
                "requires_confirmation": True,
                "language": "ru",
                "generator": {"provider": "mock", "model": "m", "is_mock": True},
            },
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


def _ok(response: Any) -> Any:
    assert response.status_code in (200, 201, 202), response.content
    return response.json()


def _deal(client: APIClient) -> str:
    if not client.get("/api/v1/sales/pipelines/").json():
        stages = [{"name": "New", "probability": 10}, {"name": "Won", "probability": 100}]
        _ok(
            client.post(
                "/api/v1/sales/pipelines/",
                {"name": "P", "is_default": True, "stages": stages},
                format="json",
            )
        )
    deal_id: str = _ok(
        client.post(
            "/api/v1/sales/deals/", {"title": "Внедрение", "amount": "10.00"}, format="json"
        )
    )["id"]
    return deal_id


def _plan(
    client: APIClient, ai: FakeAiPlatform, deal_id: str, actions: list[dict[str, Any]]
) -> str:
    ai.actions = actions
    run = _ok(
        client.post(
            "/api/v1/ai/agent/runs/",
            {"request": "Поставь задачу перезвонить завтра", "deal_id": deal_id},
            format="json",
        )
    )
    with system_tenant_scope("acme"):
        service = composition.agent_service()
        service.submit(UUID(run["id"]))
        service.poll(UUID(run["id"]))
    run_id: str = run["id"]
    return run_id


def test_plan_is_rechecked_and_runs_only_on_confirmation(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    client = client_for(owner, "acme")
    deal_id = _deal(client)
    ref = f"deal:{deal_id}"
    run_id = _plan(
        client,
        ai,
        deal_id,
        [
            {
                "tool": "create_task",
                "subject_ref": ref,
                "title": "Перезвонить",
                "due_date": None,
                "reason": "r",
            },
            {"tool": "log_note", "subject_ref": ref, "body": "Клиент ждёт звонка", "reason": "r"},
            # Not offered to the AI in this shape: an unknown record is dropped by the CRM.
            {
                "tool": "create_task",
                "subject_ref": f"deal:{uuid4()}",
                "title": "X",
                "due_date": None,
                "reason": "r",
            },
        ],
    )
    payload = ai.submits[0]["payload"]
    assert ai.submits[0]["job_type"] == "agent.plan.v1"
    assert payload["subjects"] == [{"ref": ref, "kind": "deal", "title": "Внедрение"}]
    assert set(payload["tools"]) == {"create_task", "log_note", "request_deal_brief"}

    run = client.get(f"/api/v1/ai/agent/runs/{run_id}/").json()
    assert run["requires_confirmation"] is True
    assert [a["status"] for a in run["actions"]] == ["proposed", "proposed"]
    # Proposals alone change nothing.
    tasks = ActivityRecord.objects.using(ALIAS).filter(subject_id=deal_id)
    assert tasks.count() == 0

    done = _ok(client.post(f"/api/v1/ai/agent/runs/{run_id}/actions/0/confirm/"))
    assert done["status"] == "executed" and done["result_ref"].startswith("activity:")
    task = ActivityRecord.objects.using(ALIAS).get(subject_id=deal_id)
    assert task.kind == "task" and task.title == "Перезвонить" and task.owner_user_id == owner.pk
    # Confirming again never runs it twice.
    again = _ok(client.post(f"/api/v1/ai/agent/runs/{run_id}/actions/0/confirm/"))
    assert again["result_ref"] == done["result_ref"]
    assert ActivityRecord.objects.using(ALIAS).filter(subject_id=deal_id).count() == 1

    rejected = _ok(client.post(f"/api/v1/ai/agent/runs/{run_id}/actions/1/reject/"))
    assert rejected["status"] == "rejected"
    assert (
        client.post(f"/api/v1/ai/agent/runs/{run_id}/actions/1/confirm/").json()["status"]
        == "rejected"
    )
    assert ActivityRecord.objects.using(ALIAS).filter(subject_id=deal_id).count() == 1
    assert client.post(f"/api/v1/ai/agent/runs/{run_id}/actions/7/confirm/").status_code == 409

    actions = set(
        AuditRecord.objects.using(ALIAS)
        .filter(resource_type="agent_run")
        .values_list("action", flat=True)
    )
    assert {"agent_run.requested", "agent_action.executed", "agent_action.rejected"} <= actions


def test_runs_are_personal_and_follow_rights(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    admin = make_user("admin", tenant=tenants["acme"], role=Role.ADMIN)
    rep = make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP)
    viewer = make_user("viewer", tenant=tenants["acme"], role=Role.VIEWER)
    deal_id = _deal(client_for(owner, "acme"))
    run_id = _plan(
        client_for(owner, "acme"),
        ai,
        deal_id,
        [{"tool": "log_note", "subject_ref": f"deal:{deal_id}", "body": "b", "reason": "r"}],
    )
    # Nobody else can read or confirm someone's run, not even an admin.
    assert client_for(admin, "acme").get(f"/api/v1/ai/agent/runs/{run_id}/").status_code == 404
    assert (
        client_for(admin, "acme")
        .post(f"/api/v1/ai/agent/runs/{run_id}/actions/0/confirm/")
        .status_code
        == 404
    )
    # A rep cannot name a deal they cannot see; a viewer may not use the assistant.
    body = {"request": "заметка", "deal_id": deal_id}
    assert (
        client_for(rep, "acme").post("/api/v1/ai/agent/runs/", body, format="json").status_code
        == 404
    )
    assert (
        client_for(viewer, "acme").post("/api/v1/ai/agent/runs/", body, format="json").status_code
        == 403
    )
    stranger = make_user("stranger", tenant=tenants["globex"])
    assert client_for(stranger, "globex").get(f"/api/v1/ai/agent/runs/{run_id}/").status_code == 404
    assert AgentRunRecord.objects.using(ALIAS).count() == 1


def test_customer_names_are_not_sent(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    client = client_for(owner, "acme")
    customer = _ok(client.post("/api/v1/customers/", {"name": "Айгерим Садыкова"}, format="json"))
    _ok(
        client.post(
            "/api/v1/ai/agent/runs/",
            {"request": "Запиши заметку", "customer_id": customer["id"]},
            format="json",
        )
    )
    run = AgentRunRecord.objects.using(ALIAS).get()
    assert "Айгерим" not in str(run.payload)
    assert run.payload["subjects"][0]["kind"] == "customer"
