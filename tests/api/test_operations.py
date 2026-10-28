"""Operations findings API: rules over real records, role visibility, company thresholds."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.activities.infrastructure.models import ActivityRecord
from modules.audit.infrastructure.models import AuditRecord
from modules.leads.infrastructure.models import LeadRecord
from modules.sales_pipeline.infrastructure.models import DealRecord
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)

URL = "/api/v1/operations/findings/"
SETTINGS_URL = "/api/v1/operations/settings/"


def _ok(response: Any) -> dict[str, Any]:
    assert response.status_code in (200, 201), response.content
    data: dict[str, Any] = response.json()
    return data


def _lead(client: APIClient, alias: str, *, age_days: int) -> str:
    lead_id: str = _ok(
        client.post("/api/v1/leads/", {"name": "L", "email": "l@example.test"}, format="json")
    )["id"]
    LeadRecord.objects.using(alias).filter(id=lead_id).update(
        updated_at=datetime.now(UTC) - timedelta(days=age_days)
    )
    return lead_id


def _deal(client: APIClient, alias: str, *, age_days: int) -> str:
    stages = [{"name": "New", "probability": 10}, {"name": "Won", "probability": 90}]
    if not client.get("/api/v1/sales/pipelines/").json():  # a plain list, not paginated
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
    DealRecord.objects.using(alias).filter(id=deal_id).update(
        updated_at=datetime.now(UTC) - timedelta(days=age_days)
    )
    return deal_id


def _task(client: APIClient, alias: str, *, deal_id: str, due: date) -> str:
    task_id: str = _ok(
        client.post(
            "/api/v1/activities/",
            {"kind": "task", "subject_type": "deal", "subject_id": deal_id, "title": "Call back"},
            format="json",
        )
    )["id"]
    ActivityRecord.objects.using(alias).filter(id=task_id).update(due_date=due)
    return task_id


def _kinds(report: dict[str, Any]) -> set[tuple[str, str]]:
    return {(f["kind"], f["record_id"]) for f in report["findings"]}


def test_findings_over_real_records(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    a = "tenant_acme"
    old_lead = _lead(client, a, age_days=10)
    _lead(client, a, age_days=0)
    lonely_deal = _deal(client, a, age_days=1)
    planned_deal = _deal(client, a, age_days=1)
    _task(client, a, deal_id=planned_deal, due=datetime.now(UTC).date() + timedelta(days=2))
    stalled_deal = _deal(client, a, age_days=30)
    late_task = _task(
        client, a, deal_id=stalled_deal, due=datetime.now(UTC).date() - timedelta(days=4)
    )

    report = _ok(client.get(URL))
    assert _kinds(report) == {
        ("stale_lead", old_lead),
        ("deal_without_next_step", lonely_deal),
        ("stalled_deal", stalled_deal),
        # Its only task is overdue, so the stalled deal has no next step either.
        ("deal_without_next_step", stalled_deal),
        ("overdue_task", late_task),
    }
    assert report["counts"]["deal_without_next_step"] == 2
    assert report["skipped"] == [] and report["truncated"] is False
    # Ids and ages only: no names, amounts or free text leave this endpoint.
    assert set(report["findings"][0]) == {
        "kind",
        "record_type",
        "record_id",
        "owner_user_id",
        "age_days",
        "deal_id",
    }


def test_visibility_follows_roles(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    rep_user = make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP)
    rep = client_for(rep_user, "acme")
    owners_lead = _lead(owner, "tenant_acme", age_days=10)
    reps_lead = _lead(rep, "tenant_acme", age_days=10)

    assert {f["record_id"] for f in _ok(rep.get(URL))["findings"]} == {reps_lead}
    assert {owners_lead, reps_lead} <= {f["record_id"] for f in _ok(owner.get(URL))["findings"]}


def test_company_thresholds(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    manager = client_for(make_user("mgr", tenant=tenants["acme"], role=Role.MANAGER), "acme")
    lead = _lead(owner, "tenant_acme", age_days=5)

    assert _ok(owner.get(SETTINGS_URL)) == {"stale_lead_days": 3, "stalled_deal_days": 14}
    assert ("stale_lead", lead) in _kinds(_ok(owner.get(URL)))

    body = {"stale_lead_days": 7, "stalled_deal_days": 21}
    assert manager.put(SETTINGS_URL, body, format="json").status_code == 403
    assert _ok(owner.put(SETTINGS_URL, body, format="json")) == body
    assert ("stale_lead", lead) not in _kinds(_ok(owner.get(URL)))
    assert _ok(manager.get(SETTINGS_URL)) == body
    assert (
        AuditRecord.objects.using("tenant_acme")
        .filter(action="operations_settings.updated")
        .exists()
    )

    bad = owner.put(SETTINGS_URL, {"stale_lead_days": 0, "stalled_deal_days": 21}, format="json")
    assert bad.status_code == 400


def test_thresholds_and_findings_are_per_company(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    alice = client_for(make_user("alice", tenant=tenants["acme"]), "acme")
    bob = client_for(make_user("bob", tenant=tenants["globex"]), "globex")
    globex_lead = _lead(bob, "tenant_globex", age_days=10)
    alice.put(SETTINGS_URL, {"stale_lead_days": 30, "stalled_deal_days": 30}, format="json")

    assert _ok(bob.get(SETTINGS_URL))["stale_lead_days"] == 3
    assert _ok(alice.get(URL))["findings"] == []
    assert ("stale_lead", globex_lead) in _kinds(_ok(bob.get(URL)))
    # Alice naming Globex: not a member, so the tenant does not exist for her.
    assert (
        client_for(make_user("alice2", tenant=tenants["acme"]), "globex").get(URL).status_code
        == 404
    )
