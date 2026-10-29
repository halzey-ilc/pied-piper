"""AI operations digests: snapshot content, RBAC, per-user privacy, daily run, isolation.

The AI Platform is replaced by a recording fake; no network is used.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.ai_insights import composition
from modules.ai_insights.application.ports import AiJobState
from modules.ai_insights.infrastructure.models import AiReportRecord
from modules.leads.infrastructure.models import LeadRecord
from platform_core.infrastructure.tenancy.services import system_tenant_scope
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)

URL = "/api/v1/ai/operations/digest/"


@dataclass
class FakeAiPlatform:
    submits: list[dict[str, Any]] = field(default_factory=list)

    def submit(self, **kwargs: Any) -> AiJobState:
        self.submits.append(kwargs)
        return AiJobState(job_id=f"job-{len(self.submits)}", status="queued")

    def fetch(self, **kwargs: Any) -> AiJobState:
        payload = self.submits[-1]["payload"]
        refs = [f["ref"] for f in payload["findings"]][:3]
        return AiJobState(
            job_id=kwargs["job_id"],
            status="succeeded",
            schema_id="operations_digest.output.v1",
            provider="mock",
            model="mock-extractive-v1",
            output={
                "schema": "operations_digest.output.v1",
                "headline": "h",
                "items": [{"kind": "stale_lead", "text": "t", "finding_refs": refs}],
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


def _stale_lead(client: APIClient, alias: str, name: str = "Ivan Petrov") -> str:
    response = client.post(
        "/api/v1/leads/", {"name": name, "email": "lead@example.test"}, format="json"
    )
    assert response.status_code == 201, response.content
    lead_id: str = response.json()["id"]
    LeadRecord.objects.using(alias).filter(id=lead_id).update(
        updated_at=datetime.now(UTC) - timedelta(days=10)
    )
    return lead_id


def _run_worker(slug: str, digest_id: str) -> None:
    with system_tenant_scope(slug):
        service = composition.ai_report_service()
        service.submit(UUID(digest_id))
        service.poll(UUID(digest_id))


def test_request_sends_ids_only_and_stores_explanation(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    client = client_for(owner, "acme")
    lead_id = _stale_lead(client, "tenant_acme", name="Secret Client Name")

    created = client.post(URL, {"language": "en"}, format="json")
    assert created.status_code == 202, created.content
    body = created.json()
    assert body["status"] == "pending" and body["kind"] == "operations_digest"
    assert body["input"]["counts"]["stale_lead"] == 1

    _run_worker("acme", body["id"])
    sent = ai.submits[0]
    assert sent["job_type"] == "operations.digest.v1"
    assert sent["tenant_slug"] == "acme" and sent["subject"] == f"user:{owner.id}"
    assert sent["payload"]["language"] == "en"
    assert sent["payload"]["findings"] == [
        {"ref": f"lead:{lead_id}", "kind": "stale_lead", "age_days": 10}
    ]
    # Names, e-mails and other content never leave the CRM.
    assert "Secret Client Name" not in str(sent) and "example.test" not in str(sent)

    latest = client.get(URL).json()
    assert latest["status"] == "succeeded" and latest["is_mock"] is True
    assert latest["result"]["items"][0]["finding_refs"] == [f"lead:{lead_id}"]

    again = client.post(URL, {"language": "en"}, format="json")
    assert again.status_code == 200 and again.json()["id"] == body["id"]


def test_rbac_privacy_and_switch(
    settings: Any,
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    rep_user = make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP)
    rep = client_for(rep_user, "acme")
    viewer = client_for(make_user("viewer", tenant=tenants["acme"], role=Role.VIEWER), "acme")
    owners_lead = _stale_lead(owner, "tenant_acme")
    reps_lead = _stale_lead(rep, "tenant_acme")

    assert viewer.post(URL, {}, format="json").status_code == 403
    rep_digest = rep.post(URL, {}, format="json").json()
    record = AiReportRecord.objects.using("tenant_acme").get(id=rep_digest["id"])
    refs = {f["ref"] for f in record.payload["findings"]}
    # A sales rep's digest covers their own leads only.
    assert refs == {f"lead:{reps_lead}"} and f"lead:{owners_lead}" not in refs
    # Digests are personal: the owner has none yet, and cannot read the rep's.
    assert owner.get(URL).status_code == 404

    settings.AI_PLATFORM = {**settings.AI_PLATFORM, "ENABLED": False}
    response = owner.post(URL, {}, format="json")
    assert response.status_code == 409 and response.json()["error"]["code"] == "ai_disabled"
    assert owner.post(URL, {"language": "de"}, format="json").status_code == 400


def test_daily_run_is_targeted_and_idempotent(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    rep = make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP)
    make_user("viewer", tenant=tenants["acme"], role=Role.VIEWER)  # no ai.request
    make_user("idle", tenant=tenants["acme"], role=Role.SALES_REP)  # no findings
    _stale_lead(client_for(rep, "acme"), "tenant_acme")

    with system_tenant_scope("acme"):
        first = composition.ai_report_service().create_daily_digests()
        second = composition.ai_report_service().create_daily_digests()
    assert (first, second) == (2, 0)  # owner sees the rep's lead too; rerun adds nothing
    users = set(
        AiReportRecord.objects.using("tenant_acme")
        .filter(trigger="daily")
        .values_list("requested_by", flat=True)
    )
    assert users == {owner.id, rep.id}


def test_digest_never_mixes_companies(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    alice = client_for(make_user("alice", tenant=tenants["acme"]), "acme")
    bob = client_for(make_user("bob", tenant=tenants["globex"]), "globex")
    globex_lead = _stale_lead(bob, "tenant_globex")
    acme_lead = _stale_lead(alice, "tenant_acme")

    digest_id = alice.post(URL, {}, format="json").json()["id"]
    record = AiReportRecord.objects.using("tenant_acme").get(id=digest_id)
    refs = {f["ref"] for f in record.payload["findings"]}
    assert refs == {f"lead:{acme_lead}"} and f"lead:{globex_lead}" not in refs
    assert not AiReportRecord.objects.using("tenant_globex").exists()
    _run_worker("acme", digest_id)
    assert ai.submits[0]["tenant_slug"] == "acme"
