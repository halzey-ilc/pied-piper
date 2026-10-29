"""AI reply suggestions (Communication Assistant): what is sent, RBAC, isolation and
the guarantee that nothing is sent to the customer.

The AI Platform is replaced by a recording fake; no network is used.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from django.forms.models import model_to_dict
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.ai_insights import composition
from modules.ai_insights.application.ports import AiJobState
from modules.ai_insights.infrastructure.models import ReplySuggestionRecord
from modules.audit.infrastructure.models import AuditRecord
from modules.communications.infrastructure.models import ConversationRecord, MessageRecord
from platform_core.infrastructure.tenancy.services import system_tenant_scope
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)

PHONE = "+996555123456"
OUTPUT: dict[str, Any] = {
    "schema": "message_reply.output.v1",
    "intent": "order",
    "urgency": "normal",
    "evidence": {"message_ref": "x", "quote": "Хочу оформить заказ"},
    "draft_reply": "Здравствуйте! Менеджер уточнит детали.",
    "needs_human": True,
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
            schema_id="message_reply.output.v1",
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


def _conversation(alias: str, owner_id: Any, *texts: tuple[str, str]) -> ConversationRecord:
    start = datetime.now(UTC) - timedelta(hours=1)
    conversation = ConversationRecord.objects.using(alias).create(
        id=uuid4(),
        channel="whatsapp",
        external_contact_id=f"{PHONE}{uuid4().int % 1000:03d}",
        contact_name="Айгерим Садыкова",
        owner_user_id=owner_id,
        created_at=start,
        last_message_at=start,
    )
    for i, (direction, text) in enumerate(texts):
        _message(alias, conversation, direction, text, start + timedelta(minutes=i))
    return conversation


def _message(
    alias: str, conversation: ConversationRecord, direction: str, text: str, at: datetime
) -> None:
    MessageRecord.objects.using(alias).create(
        id=uuid4(),
        conversation_id=conversation.id,
        channel=conversation.channel,
        direction=direction,
        text=text,
        status="received" if direction == "inbound" else "sent",
        created_at=at,
    )


CHAT = (
    ("inbound", "Здравствуйте! Интересует тариф Бизнес."),
    ("outbound", "Добрый день! Тариф Бизнес стоит 4500 сом в месяц."),
    ("inbound", "Хочу оформить заказ."),
)


def _url(conversation: ConversationRecord) -> str:
    return f"/api/v1/ai/conversations/{conversation.id}/reply/"


def _run_worker(slug: str, suggestion_id: str) -> None:
    with system_tenant_scope(slug):
        service = composition.reply_suggestion_service()
        service.submit(UUID(suggestion_id))
        service.poll(UUID(suggestion_id))


def test_sends_messages_without_contact_details_and_never_sends_to_customer(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    client = client_for(owner, "acme")
    conversation = _conversation("tenant_acme", owner.id, *CHAT)

    created = client.post(_url(conversation), {"language": "ru"}, format="json")
    assert created.status_code == 202, created.content
    body = created.json()
    assert body["status"] == "pending" and body["auto_send"] is False
    _run_worker("acme", body["id"])

    sent = ai.submits[0]
    assert sent["job_type"] == "message.reply.v1"
    assert sent["idempotency_key"] == f"crm-reply-{body['id']}"
    payload = sent["payload"]
    assert payload["channel"] == "whatsapp" and payload["language"] == "ru"
    assert [m["direction"] for m in payload["messages"]] == ["inbound", "outbound", "inbound"]
    assert payload["messages"][-1]["text"] == "Хочу оформить заказ."
    # The contact's phone number and name never reach the AI Platform.
    assert PHONE not in str(payload) and "Айгерим" not in str(payload)

    latest = client.get(_url(conversation)).json()
    assert latest["status"] == "succeeded" and latest["result"]["intent"] == "order"
    assert latest["is_mock"] is True
    # The suggestion added no message to the conversation.
    assert (
        MessageRecord.objects.using("tenant_acme").filter(conversation_id=conversation.id).count()
        == 3
    )
    # Message texts are not copied into the suggestion record.
    row = ReplySuggestionRecord.objects.using("tenant_acme").get(id=body["id"])
    stored = {k: v for k, v in model_to_dict(row).items() if k != "result"}
    assert "оформить" not in str(stored)
    actions = set(
        AuditRecord.objects.using("tenant_acme")
        .filter(resource_type="reply_suggestion")
        .values_list("action", flat=True)
    )
    assert actions == {"reply_suggestion.requested", "reply_suggestion.succeeded"}


def test_repeat_request_reuses_until_a_new_message_arrives(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    client = client_for(owner, "acme")
    conversation = _conversation("tenant_acme", owner.id, *CHAT)
    first = client.post(_url(conversation)).json()
    again = client.post(_url(conversation))
    assert again.status_code == 200 and again.json()["id"] == first["id"]

    _message("tenant_acme", conversation, "inbound", "Ответьте, пожалуйста.", datetime.now(UTC))
    newer = client.post(_url(conversation))
    assert newer.status_code == 202 and newer.json()["id"] != first["id"]


def test_conversation_changed_before_submit_is_not_sent(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    conversation = _conversation("tenant_acme", owner.id, *CHAT)
    suggestion_id = client_for(owner, "acme").post(_url(conversation)).json()["id"]
    # A manager answered meanwhile: the suggestion would answer an old state.
    _message("tenant_acme", conversation, "outbound", "Оформили, спасибо!", datetime.now(UTC))
    _run_worker("acme", suggestion_id)
    assert ai.submits == []
    row = ReplySuggestionRecord.objects.using("tenant_acme").get(id=suggestion_id)
    assert row.status == "failed" and row.error_code == "source_changed"


def test_nothing_to_answer_when_company_wrote_last(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    conversation = _conversation("tenant_acme", owner.id, *CHAT[:2])
    assert client_for(owner, "acme").post(_url(conversation)).status_code == 400
    assert ReplySuggestionRecord.objects.using("tenant_acme").count() == 0


def test_only_the_latest_twenty_messages_are_sent(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    texts = [("inbound", f"Сообщение номер {i}.") for i in range(30)]
    conversation = _conversation("tenant_acme", owner.id, *texts)
    suggestion_id = client_for(owner, "acme").post(_url(conversation)).json()["id"]
    _run_worker("acme", suggestion_id)
    messages = ai.submits[0]["payload"]["messages"]
    assert len(messages) == 20
    assert messages[0]["text"] == "Сообщение номер 10." and messages[-1]["text"].endswith("29.")


def test_rbac_and_isolation(
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    rep = make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP)
    viewer = make_user("viewer", tenant=tenants["acme"], role=Role.VIEWER)
    owners = _conversation("tenant_acme", owner.id, *CHAT)
    reps = _conversation("tenant_acme", rep.id, *CHAT)
    unassigned = _conversation("tenant_acme", None, *CHAT)

    assert client_for(viewer, "acme").post(_url(reps)).status_code == 403
    assert client_for(rep, "acme").post(_url(owners)).status_code == 404
    # Unassigned conversations are visible, but a rep must take one before using AI on it.
    assert client_for(rep, "acme").post(_url(unassigned)).status_code == 403
    assert client_for(rep, "acme").post(_url(reps)).status_code == 202
    assert client_for(rep, "acme").get(_url(owners)).status_code == 404

    # Another company's user gets the same 404 as for a missing conversation.
    stranger = make_user("stranger", tenant=tenants["globex"])
    assert client_for(stranger, "globex").post(_url(owners)).status_code == 404
    assert client_for(stranger, "globex").get(_url(owners)).status_code == 404


def test_disabled(
    settings: Any,
    ai: FakeAiPlatform,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    settings.AI_PLATFORM = {**settings.AI_PLATFORM, "ENABLED": False}
    conversation = _conversation("tenant_acme", owner.id, *CHAT)
    response = client_for(owner, "acme").post(_url(conversation))
    assert response.status_code == 409 and response.json()["error"]["code"] == "ai_disabled"
