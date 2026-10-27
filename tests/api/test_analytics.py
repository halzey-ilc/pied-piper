"""Analytics API: KPI computation, async report jobs, RBAC and tenant isolation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.reporting.composition import report_service
from modules.sales_kpi.infrastructure.models import MetricFact
from platform_core.infrastructure.tenancy.services import system_tenant_scope
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)

_WHEN = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)


def _seed_facts(alias: str) -> None:
    rows: list[MetricFact] = []

    def add(metric: str, value: str, times: int = 1) -> None:
        for _ in range(times):
            rows.append(
                MetricFact(
                    source_event_id=uuid4(),
                    metric=metric,
                    value=Decimal(value),
                    occurred_at=_WHEN,
                    owner_user_id=None,
                )
            )

    add("deal_won", "1", 3)
    add("deal_closed", "1", 4)  # 3 won + 1 lost
    add("revenue_won", "1500.00")
    add("lead_captured", "1", 10)
    add("lead_converted", "1", 2)
    MetricFact.objects.using(alias).bulk_create(rows)


def test_kpis_computed_from_facts(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    _seed_facts("tenant_acme")
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    response = client.get("/api/v1/analytics/kpis/?period=month&start=2026-03-01")
    assert response.status_code == 200
    kpis = {k["code"]: k["value"] for k in response.json()["kpis"]}
    assert kpis["deals_won"] == "3"
    assert kpis["revenue_won"] == "1500.00"
    assert kpis["win_rate"] == "0.75"
    assert kpis["lead_conversion_rate"] == "0.2"
    # Facts outside the period are not counted.
    empty = client.get("/api/v1/analytics/kpis/?period=month&start=2026-04-01")
    assert empty.json()["kpis"][0]["value"] in (None, "0")


def test_kpi_access_requires_reports_view(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    _seed_facts("tenant_acme")
    viewer = client_for(make_user("viewer", tenant=tenants["acme"], role=Role.VIEWER), "acme")
    rep = client_for(make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP), "acme")
    assert viewer.get("/api/v1/analytics/kpis/").status_code == 200
    assert rep.get("/api/v1/analytics/kpis/").status_code == 403


def test_report_job_lifecycle_and_download(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    _seed_facts("tenant_acme")
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    body = {
        "report_type": "kpi_summary",
        "period_start": "2026-03-01",
        "period_end": "2026-03-31",
        "idempotency_key": "kpi-2026-03",
    }
    created = client.post("/api/v1/analytics/reports/", body, format="json")
    assert created.status_code == 202
    job_id = created.json()["id"]
    assert created.json()["status"] == "queued"

    # Idempotent: same key returns the same job with 200.
    again = client.post("/api/v1/analytics/reports/", body, format="json")
    assert again.status_code == 200 and again.json()["id"] == job_id

    # Simulate the worker rendering the report (the on-commit enqueue does not run in tests).
    with system_tenant_scope("acme"):
        report_service().run(UUID(job_id))

    job_status = client.get(f"/api/v1/analytics/reports/{job_id}/")
    assert job_status.status_code == 200 and job_status.json()["status"] == "succeeded"
    assert job_status.json()["download_url"] is not None

    download = client.get(f"/api/v1/analytics/reports/{job_id}/download/")
    assert download.status_code == 200
    assert download["Content-Type"] == "text/csv"
    text = download.content.decode()
    assert "kpi_code" in text
    assert "revenue_won" in text and "1500.00" in text


def test_report_rbac(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    rep = client_for(make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP), "acme")
    viewer = client_for(make_user("viewer", tenant=tenants["acme"], role=Role.VIEWER), "acme")
    body = {
        "report_type": "kpi_summary",
        "period_start": "2026-03-01",
        "period_end": "2026-03-31",
        "idempotency_key": "rbac-key",
    }
    # Sales reps cannot generate or view.
    assert rep.post("/api/v1/analytics/reports/", body, format="json").status_code == 403
    # Viewers cannot generate (no reports.generate).
    assert viewer.post("/api/v1/analytics/reports/", body, format="json").status_code == 403


def test_analytics_tenant_isolation(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    _seed_facts("tenant_acme")
    alice = make_user("alice", tenant=tenants["acme"])
    # Not a member of globex -> 404 for analytics there too.
    assert client_for(alice, "globex").get("/api/v1/analytics/kpis/").status_code == 404
    # globex has no facts of its own.
    assert MetricFact.objects.using("tenant_globex").count() == 0
