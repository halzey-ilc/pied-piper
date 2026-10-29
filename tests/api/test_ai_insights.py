"""AI call summaries: RBAC, tenant isolation, idempotency and the worker steps.

The AI Platform is replaced by a recording fake; no network is used.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.ai_insights import composition
from modules.ai_insights.application.ports import AiJobState
from modules.ai_insights.infrastructure.models import CallInsightRecord
from modules.audit.infrastructure.models import AuditRecord
from modules.telephony.infrastructure.models import CallRecord
from platform_core.infrastructure.messaging.models import OutboxMessage
from platform_core.infrastructure.tenancy.services import system_tenant_scope
from shared.application.retry import PermanentError, TransientError
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)

NOTES = "Hello. The client needs an ERP integration. I will call back on Friday."
OUTPUT: dict[str, Any] = {
    "schema": "call_summary.output.v1",
    "summary": "Hello.",
    "key_points": [],
    "next_step": None,
    "language": "unknown",
    "generator": {"provider": "mock", "model": "mock-extractive-v1", "is_mock": True},
}


@dataclass
class FakeAiPlatform:
    submits: list[dict[str, Any]] = field(default_factory=list)
    fetches: list[dict[str, Any]] = field(default_factory=list)
    next_status: str = "succeeded"
    submit_error: Exception | None = None

    def submit(self, **kwargs: Any) -> AiJobState:
        if self.submit_error is not None:
            raise self.submit_error
        self.submits.append(kwargs)
        return AiJobState(job_id=f"job-{len(self.submits)}", status="queued")

    def fetch(self, **kwargs: Any) -> AiJobState:
        self.fetches.append(kwargs)
        if self.next_status != "succeeded":
            return AiJobState(job_id=kwargs["job_id"], status=self.next_status)
        return AiJobState(
            job_id=kwargs["job_id"],
            status="succeeded",
            schema_id="call_summary.output.v1",
            provider="mock",
            model="mock-extractive-v1",
            output=OUTPUT,
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


def _call(alias: str, owner_id: Any, notes: str | None = NOTES) -> CallRecord:
    from datetime import UTC, datetime
    from uuid import uuid4

    return CallRecord.objects.using(alias).create(
        id=uuid4(),
        direction="inbound",
        from_number="+77011112233",
        to_number="+77019998877",
        status="completed",
        owner_user_id=owner_id,
        notes=notes,
        created_at=datetime.now(UTC),
    )


def _url(call: CallRecord) -> str:
    return f"/api/v1/ai/calls/{call.id}/summary/"


def _run_worker(slug: str, insight_id: str) -> None:
    with system_tenant_scope(slug):
        service = composition.call_insight_service()
        service.submit(_uuid(insight_id))
        service.poll(_uuid(insight_id))


def _uuid(value: str) -> Any:
    from uuid import UUID

    return UUID(value)


def test_request_then_worker_stores_structured_result(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    client = client_for(owner, "acme")
    call = _call("tenant_acme", owner.id)

    created = client.post(_url(call))
    assert created.status_code == 202, created.content
    body = created.json()
    assert body["status"] == "pending" and body["result"] is None
    # Nothing left the CRM inside the request: submission happens in the worker.
    assert ai.submits == []

    _run_worker("acme", body["id"])
    assert len(ai.submits) == 1
    sent = ai.submits[0]
    assert sent["tenant_slug"] == "acme" and sent["subject"] == f"user:{owner.id}"
    assert sent["payload"]["transcript"] == NOTES
    assert sent["payload"]["language"] == "ru"  # the company language, not "unknown"
    assert sent["idempotency_key"] == f"crm-insight-{body['id']}"

    latest = client.get(_url(call)).json()
    assert latest["status"] == "succeeded"
    assert latest["is_mock"] is True and latest["result"]["summary"] == "Hello."
    # The free-text summary is never presented as verified (only quotes are checked).
    assert latest["summary_grounded"] is False
    assert (
        OutboxMessage.objects.using("tenant_acme")
        .filter(event_type="ai_insights.call_insight_completed")
        .exists()
    )
    actions = set(
        AuditRecord.objects.using("tenant_acme")
        .filter(resource_type="call_insight")
        .values_list("action", flat=True)
    )
    assert actions == {"call_insight.requested", "call_insight.succeeded"}


def test_repeat_request_reuses_insight(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    client = client_for(owner, "acme")
    call = _call("tenant_acme", owner.id)
    first = client.post(_url(call)).json()
    again = client.post(_url(call))
    assert again.status_code == 200 and again.json()["id"] == first["id"]
    assert CallInsightRecord.objects.using("tenant_acme").count() == 1


def test_worker_steps_are_idempotent(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    call = _call("tenant_acme", owner.id)
    insight_id = client_for(owner, "acme").post(_url(call)).json()["id"]
    _run_worker("acme", insight_id)
    _run_worker("acme", insight_id)  # redelivered messages
    assert len(ai.submits) == 1 and len(ai.fetches) == 1


def test_still_running_job_is_polled_again(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
    django_capture_on_commit_callbacks: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        "modules.ai_insights.infrastructure.scheduler.current_app.send_task",
        lambda name, **kw: sent.append((name, kw.get("countdown"))),
    )
    ai.next_status = "running"
    owner = make_user("owner", tenant=tenants["acme"])
    call = _call("tenant_acme", owner.id)
    # The scheduler registers on_commit on the tenant database, not "default".
    with django_capture_on_commit_callbacks(using="tenant_acme", execute=True):
        insight_id = client_for(owner, "acme").post(_url(call)).json()["id"]
    assert sent == [("integrations.ai_submit_insight", None)]
    with django_capture_on_commit_callbacks(using="tenant_acme", execute=True):
        _run_worker("acme", insight_id)
    assert [name for name, _ in sent[1:]] == [
        "integrations.ai_poll_insight",
        "integrations.ai_poll_insight",
    ]
    row = CallInsightRecord.objects.using("tenant_acme").get(id=insight_id)
    assert row.status == "submitted"


def test_rejected_submission_fails_without_retry(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    ai.submit_error = PermanentError("invalid_input")
    owner = make_user("owner", tenant=tenants["acme"])
    client = client_for(owner, "acme")
    call = _call("tenant_acme", owner.id)
    insight_id = client.post(_url(call)).json()["id"]
    _run_worker("acme", insight_id)
    latest = client.get(_url(call)).json()
    assert latest["status"] == "failed" and latest["error_code"] == "submit_rejected"


def test_unreachable_ai_platform_raises_for_celery_retry(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    ai.submit_error = TransientError("connect refused")
    owner = make_user("owner", tenant=tenants["acme"])
    call = _call("tenant_acme", owner.id)
    insight_id = client_for(owner, "acme").post(_url(call)).json()["id"]
    with system_tenant_scope("acme"), pytest.raises(TransientError):
        composition.call_insight_service().submit(_uuid(insight_id))
    row = CallInsightRecord.objects.using("tenant_acme").get(id=insight_id)
    assert row.status == "pending"


def test_edited_call_is_not_sent(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    call = _call("tenant_acme", owner.id)
    insight_id = client_for(owner, "acme").post(_url(call)).json()["id"]
    CallRecord.objects.using("tenant_acme").filter(id=call.id).update(notes="Другой текст.")
    _run_worker("acme", insight_id)
    assert ai.submits == []
    row = CallInsightRecord.objects.using("tenant_acme").get(id=insight_id)
    assert row.status == "failed" and row.error_code == "source_changed"


def test_rbac(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    rep = make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP)
    viewer = make_user("viewer", tenant=tenants["acme"], role=Role.VIEWER)
    owners_call = _call("tenant_acme", owner.id)
    reps_call = _call("tenant_acme", rep.id)

    # Viewer sees calls but may not send them to AI.
    assert client_for(viewer, "acme").post(_url(owners_call)).status_code == 403
    # A sales rep cannot even see someone else's call: same 404 as a missing one.
    assert client_for(rep, "acme").post(_url(owners_call)).status_code == 404
    assert client_for(rep, "acme").post(_url(reps_call)).status_code == 202
    # Reading follows call visibility.
    assert client_for(viewer, "acme").get(_url(reps_call)).status_code == 200
    assert client_for(rep, "acme").get(_url(owners_call)).status_code == 404
    assert ai.submits == []


def test_validation_and_disabled(
    settings: Any,
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    client = client_for(owner, "acme")
    assert client.post(_url(_call("tenant_acme", owner.id, notes=None))).status_code == 400
    assert client.get(_url(_call("tenant_acme", owner.id))).status_code == 404

    settings.AI_PLATFORM = {**settings.AI_PLATFORM, "ENABLED": False}
    response = client.post(_url(_call("tenant_acme", owner.id)))
    assert response.status_code == 409 and response.json()["error"]["code"] == "ai_disabled"
