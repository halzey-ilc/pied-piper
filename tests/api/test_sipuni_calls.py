# ruff: noqa: RUF001
"""A company connects its own Sipuni: calls arrive by event, are logged and linked, and
answered recorded calls are transcribed and reviewed by the AI (fake AI Platform)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.ai_insights import composition
from modules.ai_insights.application.ports import AiJobState
from modules.telephony.infrastructure.models import CallRecord, TelephonyConnectionRecord
from platform_core.infrastructure.tenancy.services import system_tenant_scope
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)
ALIAS = "tenant_acme"


class FakeAiPlatform:
    def __init__(self) -> None:
        self.audio: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.jobs: dict[str, dict[str, Any]] = {}

    def put_audio(self, *, ref: str, data: bytes, **_: Any) -> None:
        self.audio[ref] = data

    def delete_audio(self, *, ref: str, **_: Any) -> None:
        self.deleted.append(ref)

    def submit(self, *, job_type: str, payload: dict[str, Any], **_: Any) -> AiJobState:
        job_id = f"job-{len(self.jobs) + 1}"
        self.jobs[job_id] = {"type": job_type, "payload": payload}
        return AiJobState(job_id=job_id, status="queued")

    def fetch(self, *, job_id: str, **_: Any) -> AiJobState:
        job = self.jobs[job_id]
        gen = {"provider": "llamacpp", "model": "m", "is_mock": False}
        if job["type"] == "call.transcribe.v1":
            output = {
                "schema": "call_transcript.output.v1",
                "segments": [
                    {
                        "start_s": 0,
                        "end_s": 3,
                        "speaker": "manager",
                        "text": "Здравствуйте, меня зовут Айбек.",
                    },
                    {"start_s": 3, "end_s": 6, "speaker": "customer", "text": "Нам нужна CRM."},
                ],
                "text": "…",
                "duration_s": 6,
                "insufficient_data": False,
                "generator": gen,
            }
            return AiJobState(
                job_id, "succeeded", schema_id="call_transcript.output.v1", output=output
            )
        criteria = job["payload"]["criteria"]
        verdicts = [
            {
                "code": c["code"],
                "status": "met" if i == 0 else "missed",
                "evidence": {"segment_id": "s1", "quote": "меня зовут Айбек"} if i == 0 else None,
                "comment": "c",
            }
            for i, c in enumerate(criteria)
        ]
        output = {
            "schema": "call_review.output.v1",
            "criteria": verdicts,
            "strengths": [{"text": "Представился"}],
            "improvements": [{"text": "Договоритесь о следующем шаге"}],
            "insufficient_data": False,
            "language": "ru",
            "generator": gen,
        }
        return AiJobState(
            job_id, "succeeded", schema_id="call_review.output.v1", model="m", output=output
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
    monkeypatch.setattr(
        "modules.ai_insights.infrastructure.call_analysis.call_recording",
        lambda call_id: (b"ID3-fake-mp3", "audio/mpeg"),
    )
    monkeypatch.setattr(
        "modules.ai_insights.infrastructure.scheduler.current_app.send_task", lambda *a, **k: None
    )
    return fake


def _ok(response: Any) -> Any:
    assert response.status_code in (200, 201, 202), response.content
    return response.json()


def _hangup(**kw: str) -> dict[str, str]:
    event = {
        "event": "2",
        "call_id": "1700000000.42",
        "src_num": "996555123456",
        "src_type": "1",
        "dst_num": "012345101",
        "dst_type": "2",
        "timestamp": "1700000300",
        "status": "ANSWER",
        "call_start_timestamp": "1700000000",
        "call_answer_timestamp": "1700000010",
        "call_record_link": "https://sipuni.com/api/crm/record?id=1",
    }
    event.update(kw)
    return event


def _connect(client: APIClient, rep_id: Any, **kw: Any) -> dict[str, Any]:
    body = {
        "account": "012345",
        "secret": "integration-key",
        "company_number": "0312123456",
        "extensions": {"101": str(rep_id)},
        **kw,
    }
    saved: dict[str, Any] = _ok(client.put("/api/v1/telephony/sipuni/", body, format="json"))
    return saved


def test_only_a_director_connects_and_the_key_is_never_shown(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    rep_user = make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP)
    rep = client_for(rep_user, "acme")
    assert owner.get("/api/v1/telephony/sipuni/").status_code == 404
    assert rep.put("/api/v1/telephony/sipuni/", {}, format="json").status_code in (400, 403)

    saved = _connect(owner, rep_user.pk)
    assert saved["events_url"].startswith("http") and "/sipuni/events/acme/" in saved["events_url"]
    assert "secret" not in saved and saved["has_secret"] is True
    row = TelephonyConnectionRecord.objects.using(ALIAS).get()
    assert b"integration-key" not in bytes(row.encrypted_secret)

    # Changing settings keeps the key and the address; only rotate_token gives a new one.
    again = _connect(owner, rep_user.pk, secret="", call_language="ky")
    assert "events_url" not in again and again["call_language"] == "ky" and again["has_secret"]
    assert "events_url" in _connect(owner, rep_user.pk, rotate_token=True)


def test_events_log_calls_once_linked_to_the_customer_and_the_manager(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
    django_capture_on_commit_callbacks: Any,
) -> None:
    owner = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    rep_user = make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP)
    customer = _ok(
        owner.post("/api/v1/customers/", {"name": "Арча", "phone": "+996555123456"}, format="json")
    )
    url = _connect(owner, rep_user.pk)["events_url"].split("testserver")[-1]
    anonymous = APIClient()

    assert anonymous.post(url.replace("/acme/", "/acme/x"), _hangup()).status_code == 404
    assert anonymous.post(url[:-5] + "wrong/", _hangup()).status_code == 404
    assert anonymous.post(url, {**_hangup(), "event": "1"}).json() == {"success": True}
    assert CallRecord.objects.using(ALIAS).count() == 0

    with django_capture_on_commit_callbacks(using=ALIAS, execute=True):
        assert anonymous.post(url, _hangup()).json() == {"success": True}
        assert anonymous.post(url, _hangup()).json() == {"success": True}  # redelivered
    call = CallRecord.objects.using(ALIAS).get()
    assert (call.direction, call.status, call.duration_seconds) == ("inbound", "completed", 290)
    assert call.owner_user_id == rep_user.pk
    assert (call.subject_type, str(call.subject_id)) == ("customer", customer["id"])
    assert call.from_number == "+996555123456" and call.to_number == "+996312123456"

    # A malformed event is acknowledged so Sipuni keeps delivering.
    assert anonymous.post(url, {"event": "2"}).json()["success"] is True


def test_answered_call_is_transcribed_reviewed_and_scored(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
    django_capture_on_commit_callbacks: Any,
) -> None:
    owner_user = make_user("owner", tenant=tenants["acme"])
    owner = client_for(owner_user, "acme")
    url = _connect(owner, owner_user.pk)["events_url"].split("testserver")[-1]
    with django_capture_on_commit_callbacks(using=ALIAS, execute=True):
        APIClient().post(url, _hangup())
    call_id = str(CallRecord.objects.using(ALIAS).get().id)

    analysis = owner.get(f"/api/v1/ai/calls/{call_id}/analysis/").json()
    assert analysis["status"] == "pending"
    with system_tenant_scope("acme"):
        service = composition.call_analysis_service()
        for _ in range(3):
            service.step(UUID(analysis["id"]))

    result = owner.get(f"/api/v1/ai/calls/{call_id}/analysis/").json()
    assert result["status"] == "succeeded", result
    assert [s["speaker"] for s in result["segments"]] == ["manager", "customer"]
    verdicts = result["review"]["criteria"]
    assert verdicts[0]["title"] == "Приветствие и представление" and verdicts[0]["status"] == "met"
    # 1 of 5 points met: 2 of 10 possible.
    assert result["score"] == 20 and result["is_mock"] is False
    assert ai.audio and ai.deleted == list(ai.audio)
    transcribe = next(j for j in ai.jobs.values() if j["type"] == "call.transcribe.v1")
    assert transcribe["payload"]["language"] == "ru"

    # The call summary now works from the transcript, not only from notes.
    with system_tenant_scope("acme"):
        from modules.ai_insights.infrastructure.calls import TelephonyCallReader

        assert TelephonyCallReader().text_for(UUID(call_id)).startswith("Менеджер: Здравствуйте")


def test_checklist_is_the_companys_own(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    rep = client_for(make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP), "acme")
    assert len(owner.get("/api/v1/ai/calls/checklist/").json()["criteria"]) == 5
    criteria = [
        {"code": "script", "title": "По скрипту"},
        {"code": "price", "title": "Назвал цену"},
    ]
    assert (
        rep.put("/api/v1/ai/calls/checklist/", {"criteria": criteria}, format="json").status_code
        == 403
    )
    saved = _ok(owner.put("/api/v1/ai/calls/checklist/", {"criteria": criteria}, format="json"))
    assert [c["code"] for c in saved["criteria"]] == ["script", "price"]
    assert owner.get("/api/v1/ai/calls/checklist/").json()["criteria"] == saved["criteria"]
