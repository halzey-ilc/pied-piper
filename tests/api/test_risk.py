"""Risk signals and settings over the API, and the neutral AI risk review."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.ai_insights import composition
from modules.ai_insights.application.ports import AiJobState
from modules.audit.infrastructure.models import AuditRecord
from modules.sales_pipeline.infrastructure.models import DealRecord
from platform_core.infrastructure.tenancy.services import system_tenant_scope
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)
ALIAS = "tenant_acme"


def _ok(response: Any) -> Any:
    assert response.status_code in (200, 201), response.content
    return response.json()


def _won_deal_without_activity(client: APIClient) -> str:
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
        client.post("/api/v1/sales/deals/", {"title": "D", "amount": "10.00"}, format="json")
    )["id"]
    _ok(client.post(f"/api/v1/sales/deals/{deal_id}/win/"))
    return deal_id


def _audit(action: str, actor: Any, n: int) -> None:
    AuditRecord.objects.using(ALIAS).bulk_create(
        AuditRecord(
            occurred_at=datetime.now(UTC),
            actor_user_id=actor,
            action=action,
            resource_type="lead",
            resource_id=str(uuid4()),
        )
        for _ in range(n)
    )


def test_signals_from_deals_and_audit_log(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    rep = make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP)
    client = client_for(owner, "acme")
    deal_id = _won_deal_without_activity(client)
    _audit("lead.disqualified", rep.pk, 10)

    body = client.get("/api/v1/risk/signals/").json()
    assert body["is_evidence"] is False
    by_kind = {s["kind"]: s for s in body["signals"]}
    assert by_kind["won_without_activity"]["ref"] == f"deal:{deal_id}"
    assert by_kind["bulk_disqualification"]["ref"] == f"user:{rep.pk}"
    assert by_kind["bulk_disqualification"]["value"] == 10
    assert body["counts"]["won_without_activity"] == 1

    # Raising the company threshold removes the burst signal.
    settings = {**body["thresholds"], "disqualify_per_day": 20}
    assert (
        _ok(client.put("/api/v1/risk/settings/", settings, format="json"))["disqualify_per_day"]
        == 20
    )
    kinds = {s["kind"] for s in client.get("/api/v1/risk/signals/").json()["signals"]}
    assert "bulk_disqualification" not in kinds
    assert AuditRecord.objects.using(ALIAS).filter(action="risk_settings.updated").exists()


def test_amount_outlier_over_open_deals(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    client = client_for(owner, "acme")
    _won_deal_without_activity(client)  # creates the pipeline
    ids = [
        _ok(
            client.post(
                "/api/v1/sales/deals/", {"title": f"D{i}", "amount": "100.00"}, format="json"
            )
        )["id"]
        for i in range(10)
    ]
    DealRecord.objects.using(ALIAS).filter(id=ids[0]).update(amount=Decimal("900.00"))
    signals = client.get("/api/v1/risk/signals/").json()["signals"]
    [outlier] = [s for s in signals if s["kind"] == "amount_outlier"]
    assert outlier["ref"] == f"deal:{ids[0]}" and outlier["value"] == 9.0


def test_only_owner_and_admin_see_risk(
    settings: Any,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    settings.AI_PLATFORM = {**settings.AI_PLATFORM, "ENABLED": True}
    admin = make_user("admin", tenant=tenants["acme"], role=Role.ADMIN)
    assert client_for(admin, "acme").get("/api/v1/risk/signals/").status_code == 200
    for role in (Role.MANAGER, Role.SALES_REP, Role.VIEWER):
        user = make_user(role.value, tenant=tenants["acme"], role=role)
        client = client_for(user, "acme")
        assert client.get("/api/v1/risk/signals/").status_code == 403
        assert client.get("/api/v1/risk/settings/").status_code == 403
        assert client.post("/api/v1/ai/risk/review/").status_code == 403


def test_ai_risk_review_sends_ids_and_numbers_only(
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    sent: list[dict[str, Any]] = []
    output = {
        "schema": "risk_review.output.v1",
        "headline": "Signals to check.",
        "items": [],
        "checks": [],
        "insufficient_data": False,
        "is_evidence": False,
        "language": "ru",
        "generator": {"provider": "mock", "model": "m", "is_mock": True},
    }

    class Fake:
        def submit(self, **kw: Any) -> AiJobState:
            sent.append(kw)
            return AiJobState(job_id="job-1", status="queued")

        def fetch(self, **kw: Any) -> AiJobState:
            return AiJobState(
                job_id=kw["job_id"],
                status="succeeded",
                schema_id="risk_review.output.v1",
                provider="mock",
                model="m",
                output=output,
            )

    settings.AI_PLATFORM = {
        **settings.AI_PLATFORM,
        "ENABLED": True,
        "URL": "http://ai.test",
        "TOKEN_KEY": "api-test-ai-platform-key-0123456789abcdef",
    }
    monkeypatch.setattr(composition, "ai_platform_client", Fake)
    owner = make_user("owner", tenant=tenants["acme"])
    client = client_for(owner, "acme")
    deal_id = _won_deal_without_activity(client)

    created = client.post("/api/v1/ai/risk/review/", {"language": "ru"}, format="json")
    assert created.status_code == 202, created.content
    body = created.json()
    assert body["kind"] == "risk_review"
    assert body["input"]["signals"] == [
        {"ref": f"deal:{deal_id}", "kind": "won_without_activity", "value": 0, "threshold": 0}
    ]
    with system_tenant_scope("acme"):
        service = composition.ai_report_service()
        service.submit(UUID(body["id"]))
        service.poll(UUID(body["id"]))
    assert sent[0]["job_type"] == "risk.review.v1"
    latest = client.get("/api/v1/ai/risk/review/").json()
    assert latest["status"] == "succeeded" and latest["result"]["is_evidence"] is False
