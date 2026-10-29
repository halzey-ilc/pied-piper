"""AI deal briefs (Sales Coach): what the CRM computes and sends, RBAC, privacy of
briefs, and that the stored input is cleared after submission.

The AI Platform is replaced by a recording fake; no network is used.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.activities.infrastructure.models import ActivityRecord
from modules.ai_insights import composition
from modules.ai_insights.application.ports import AiJobState
from modules.ai_insights.domain.deal_brief import task_status
from modules.ai_insights.infrastructure.models import DealBriefRecord
from modules.meetings.infrastructure.models import MeetingRecord
from modules.telephony.infrastructure.models import CallRecord
from platform_core.infrastructure.tenancy.services import system_tenant_scope
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)
ALIAS = "tenant_acme"
PHONE = "+996555777888"
OUTPUT: dict[str, Any] = {
    "schema": "deal_coach.output.v1",
    "situation": [{"text": "Сделка на этапе «New».", "refs": ["deal:x"]}],
    "risks": [],
    "questions": [],
    "next_step": None,
    "insufficient_data": False,
    "language": "ru",
    "generator": {"provider": "mock", "model": "mock-extractive-v1", "is_mock": True},
}


@dataclass
class FakeAiPlatform:
    submits: list[dict[str, Any]] = field(default_factory=list)

    def submit(self, **kwargs: Any) -> AiJobState:
        self.submits.append(kwargs)
        return AiJobState(job_id=f"job-{len(self.submits)}", status="queued")

    def fetch(self, **kwargs: Any) -> AiJobState:
        return AiJobState(
            job_id=kwargs["job_id"],
            status="succeeded",
            schema_id="deal_coach.output.v1",
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


def _ok(response: Any) -> Any:
    assert response.status_code in (200, 201), response.content
    return response.json()


def _deal(client: APIClient) -> str:
    stages = [
        {"name": "New", "probability": 10},
        {"name": "Proposal", "probability": 40},
        {"name": "Won", "probability": 90},
    ]
    if not client.get("/api/v1/sales/pipelines/").json():
        _ok(
            client.post(
                "/api/v1/sales/pipelines/",
                {"name": "P", "is_default": True, "stages": stages},
                format="json",
            )
        )
    payload = {"title": "Внедрение CRM", "amount": "450000.00", "expected_close_date": "2026-01-10"}
    deal_id: str = _ok(client.post("/api/v1/sales/deals/", payload, format="json"))["id"]
    return deal_id


def _history(client: APIClient, deal_id: str, owner_id: Any) -> None:
    _ok(
        client.post(
            "/api/v1/activities/",
            {
                "kind": "note",
                "subject_type": "deal",
                "subject_id": deal_id,
                "body": "Клиент просит скидку 10%.",
            },
            format="json",
        )
    )
    task_id = _ok(
        client.post(
            "/api/v1/activities/",
            {
                "kind": "task",
                "subject_type": "deal",
                "subject_id": deal_id,
                "title": "Отправить КП",
            },
            format="json",
        )
    )["id"]
    ActivityRecord.objects.using(ALIAS).filter(id=task_id).update(
        due_date=datetime.now(UTC).date() - timedelta(days=3)
    )
    start = datetime.now(UTC) + timedelta(days=2)
    MeetingRecord.objects.using(ALIAS).create(
        id=uuid4(),
        title="Демо для директора",
        subject_type="deal",
        subject_id=deal_id,
        scheduled_start=start,
        scheduled_end=start + timedelta(hours=1),
        owner_user_id=owner_id,
        created_at=datetime.now(UTC),
    )
    CallRecord.objects.using(ALIAS).create(
        id=uuid4(),
        direction="outbound",
        from_number="+996312000000",
        to_number=PHONE,
        status="completed",
        subject_type="deal",
        subject_id=deal_id,
        owner_user_id=owner_id,
        notes="Обсудили сроки внедрения.",
        created_at=datetime.now(UTC),
    )


def _url(deal_id: str) -> str:
    return f"/api/v1/ai/deals/{deal_id}/brief/"


def _run_worker(brief_id: str) -> None:
    with system_tenant_scope("acme"):
        service = composition.deal_brief_service()
        service.submit(UUID(brief_id))
        service.poll(UUID(brief_id))


def test_task_status_is_computed_by_the_crm() -> None:
    today = date(2026, 10, 29)
    assert task_status(completed=False, due=date(2026, 10, 28), today=today) == "overdue"
    assert task_status(completed=False, due=today, today=today) == "open"
    assert task_status(completed=True, due=date(2026, 1, 1), today=today) == "done"


def test_brief_sends_deal_and_history_without_phone_numbers(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    client = client_for(owner, "acme")
    deal_id = _deal(client)
    _history(client, deal_id, owner.pk)

    created = client.post(_url(deal_id), {"language": "ru"}, format="json")
    assert created.status_code == 202, created.content
    brief_id = created.json()["id"]
    # Until submitted the input is kept; afterwards only its fingerprint.
    assert DealBriefRecord.objects.using(ALIAS).get(id=brief_id).payload is not None
    _run_worker(brief_id)
    assert DealBriefRecord.objects.using(ALIAS).get(id=brief_id).payload is None

    payload = ai.submits[0]["payload"]
    assert ai.submits[0]["job_type"] == "deal.coach.v1"
    deal = payload["deal"]
    assert deal["ref"] == f"deal:{deal_id}" and deal["stage"] == "New"
    assert (deal["stage_position"], deal["stage_count"], deal["probability"]) == (1, 3, 10)
    assert deal["amount"] == 450000.0 and deal["expected_close_date"] == "2026-01-10"
    kinds = {h["kind"]: h for h in payload["history"]}
    assert set(kinds) == {"note", "task", "meeting", "call"}
    assert kinds["task"]["status"] == "overdue"
    assert kinds["meeting"]["status"] == "scheduled"
    assert kinds["call"]["text"] == "Обсудили сроки внедрения."
    assert PHONE not in str(payload)

    latest = client.get(_url(deal_id)).json()
    assert latest["status"] == "succeeded" and latest["is_mock"] is True


def test_unchanged_deal_reuses_brief_and_new_history_does_not(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    client = client_for(owner, "acme")
    deal_id = _deal(client)
    first = client.post(_url(deal_id)).json()
    again = client.post(_url(deal_id))
    assert again.status_code == 200 and again.json()["id"] == first["id"]
    _history(client, deal_id, owner.pk)
    assert client.post(_url(deal_id)).status_code == 202


def test_briefs_are_personal_and_follow_deal_visibility(
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
    assert client_for(owner, "acme").post(_url(deal_id)).status_code == 202

    # Another user with access gets their own brief, never the owner's.
    assert client_for(admin, "acme").get(_url(deal_id)).status_code == 404
    assert client_for(rep, "acme").post(_url(deal_id)).status_code == 404  # not visible
    assert client_for(viewer, "acme").post(_url(deal_id)).status_code == 403  # no ai.request

    stranger = make_user("stranger", tenant=tenants["globex"])
    assert client_for(stranger, "globex").post(_url(deal_id)).status_code == 404


def test_disabled(
    settings: Any,
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    client = client_for(owner, "acme")
    deal_id = _deal(client)
    settings.AI_PLATFORM = {**settings.AI_PLATFORM, "ENABLED": False}
    response = client.post(_url(deal_id))
    assert response.status_code == 409 and response.json()["error"]["code"] == "ai_disabled"
