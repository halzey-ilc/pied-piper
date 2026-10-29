"""AI Business Analyst: KPI explanation requests over the real KPI read model.

The AI Platform is replaced by a recording fake; no network is used.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.ai_insights import composition
from modules.ai_insights.application.ports import AiJobState
from modules.ai_insights.infrastructure.models import AiReportRecord
from modules.sales_kpi.infrastructure.models import MetricFact
from platform_core.infrastructure.tenancy.services import system_tenant_scope
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)

URL = "/api/v1/ai/analytics/kpi-explanation/"


@dataclass
class FakeAiPlatform:
    submits: list[dict[str, Any]] = field(default_factory=list)

    def submit(self, **kwargs: Any) -> AiJobState:
        self.submits.append(kwargs)
        return AiJobState(job_id=f"job-{len(self.submits)}", status="queued")

    def fetch(self, **kwargs: Any) -> AiJobState:
        payload = self.submits[-1]["payload"]
        return AiJobState(
            job_id=kwargs["job_id"],
            status="succeeded",
            schema_id="kpi_explanation.output.v1",
            provider="mock",
            model="m",
            output={
                "schema": "kpi_explanation.output.v1",
                "headline": "h",
                "observations": [{"metric_codes": ["revenue_won"], "text": "t"}],
                "recommendations": [],
                "insufficient_data": False,
                "language": payload["language"],
                "generator": {"provider": "mock", "model": "m", "is_mock": True},
            },
        )


@pytest.fixture
def ai(settings: Any, monkeypatch: pytest.MonkeyPatch) -> FakeAiPlatform:
    settings.AI_PLATFORM = {**settings.AI_PLATFORM, "ENABLED": True, "URL": "http://ai.test"}
    fake = FakeAiPlatform()
    monkeypatch.setattr(composition, "ai_platform_client", lambda: fake)
    return fake


def _fact(alias: str, metric: str, value: str, when: datetime) -> None:
    MetricFact.objects.using(alias).create(
        source_event_id=uuid4(), metric=metric, value=Decimal(value), occurred_at=when
    )


def test_numbers_come_from_the_crm(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    client = client_for(owner, "acme")
    sep, octo = datetime(2026, 9, 10, tzinfo=UTC), datetime(2026, 10, 10, tzinfo=UTC)
    for amount in ("400.00", "600.00"):
        _fact("tenant_acme", "revenue_won", amount, sep)
    _fact("tenant_acme", "revenue_won", "1250.00", octo)

    created = client.post(URL, {"period": "month", "date": "2026-10-28"}, format="json")
    assert created.status_code == 202, created.content
    body = created.json()
    assert body["kind"] == "kpi_explanation" and body["status"] == "pending"
    revenue = next(m for m in body["input"]["metrics"] if m["code"] == "revenue_won")
    assert revenue == {
        "code": "revenue_won", "name": "Revenue won", "unit": "money",
        "current": 1250, "previous": 1000, "change": 250, "change_pct": 25,
    }  # fmt: skip
    assert body["input"]["period"]["start"] == "2026-10-01"

    with system_tenant_scope("acme"):
        service = composition.ai_report_service()
        service.submit(UUID(body["id"]))
        service.poll(UUID(body["id"]))
    sent = ai.submits[0]
    assert sent["job_type"] == "kpi.explanation.v1"
    assert sent["tenant_slug"] == "acme" and sent["subject"] == f"user:{owner.id}"
    assert sent["payload"] == body["input"]

    latest = client.get(URL).json()
    assert latest["status"] == "succeeded" and latest["result"]["observations"]
    again = client.post(URL, {"period": "month", "date": "2026-10-05"}, format="json")
    assert again.status_code == 200 and again.json()["id"] == body["id"]


def test_permissions_and_validation(
    settings: Any,
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    rep = client_for(make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP), "acme")
    viewer = client_for(make_user("viewer", tenant=tenants["acme"], role=Role.VIEWER), "acme")
    manager = client_for(make_user("mgr", tenant=tenants["acme"], role=Role.MANAGER), "acme")

    # Sales reps may use AI but not see company KPIs; viewers see KPIs but may not use AI.
    assert rep.post(URL, {}, format="json").status_code == 403
    assert viewer.post(URL, {}, format="json").status_code == 403
    assert manager.post(URL, {}, format="json").status_code == 202
    assert manager.post(URL, {"period": "year"}, format="json").status_code == 400
    assert manager.post(URL, {"language": "de"}, format="json").status_code == 400
    # Reports are personal.
    assert viewer.get(URL).status_code == 404

    settings.AI_PLATFORM = {**settings.AI_PLATFORM, "ENABLED": False}
    assert manager.post(URL, {}, format="json").status_code == 409


def test_kpis_never_mix_companies(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    alice = client_for(make_user("alice", tenant=tenants["acme"]), "acme")
    _fact("tenant_globex", "revenue_won", "999999.00", datetime(2026, 10, 10, tzinfo=UTC))
    body = alice.post(URL, {"period": "month", "date": "2026-10-28"}, format="json").json()
    revenue = next(m for m in body["input"]["metrics"] if m["code"] == "revenue_won")
    assert revenue["current"] == 0  # Globex revenue is invisible to Acme
    assert not AiReportRecord.objects.using("tenant_globex").exists()
