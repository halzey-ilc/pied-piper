"""WhatsApp through Green API: webhooks, sending, connection state and QR access."""

from __future__ import annotations

import json
from collections.abc import Callable
from io import StringIO
from typing import Any
from uuid import uuid4

import httpx
import pytest
from django.core.management import CommandError, call_command
from django.test import Client
from pydantic import SecretStr
from rest_framework.test import APIClient

from entrypoints.workers.tasks.integrations import process_webhook
from modules.access_control.domain.rbac import Role
from modules.communications.infrastructure.models import ConversationRecord, MessageRecord
from modules.leads.infrastructure.models import LeadRecord
from platform_core.infrastructure.messaging.models import InboxMessage
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)
ALIAS = "tenant_acme"
HOOK = "/api/v1/webhooks/green_api/acme/"
WEBHOOK_TOKEN = "green-webhook-token-for-tests"
API_TOKEN = "green-api-secret-token"
NUMBER = "996700000001"
CONNECTION = "modules.communications.infrastructure.connection.build_http_client"
SENDER = "modules.communications.infrastructure.sender.build_http_client"


@pytest.fixture(autouse=True)
def green_api(settings: Any) -> None:
    settings.INTEGRATIONS = {
        **settings.INTEGRATIONS,
        "SECRETS": {
            **settings.INTEGRATIONS["SECRETS"],
            "green_api": SecretStr(WEBHOOK_TOKEN),
        },
        "WHATSAPP": {
            **settings.INTEGRATIONS["WHATSAPP"],
            "PROVIDER": "green_api",
            "TENANT_SLUG": "acme",
            "LEAD_OWNER_USER_ID": uuid4(),
            "GREEN_API": {
                "URL": "https://green.example",
                "INSTANCE_ID": "7103",
                "TOKEN": SecretStr(API_TOKEN),
            },
        },
    }


def _deliver(payload: dict[str, Any], token: str = WEBHOOK_TOKEN) -> Any:
    response = Client().post(
        HOOK,
        json.dumps(payload).encode(),
        content_type="application/json",
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.status_code == 200 and not response.json()["duplicate"]:
        inbox = InboxMessage.objects.using(ALIAS).order_by("-received_at").first()
        assert inbox is not None
        process_webhook("acme", "green_api", str(inbox.id))
    return response


def _incoming(
    message_id: str,
    message_data: dict[str, Any] | None = None,
    chat: str = f"{NUMBER}@c.us",
) -> dict[str, Any]:
    return {
        "typeWebhook": "incomingMessageReceived",
        "timestamp": 1793000000,
        "idMessage": message_id,
        "senderData": {"chatId": chat, "sender": chat, "senderName": "Aida", "chatName": "Aida"},
        "messageData": message_data
        or {"typeMessage": "textMessage", "textMessageData": {"textMessage": "Здравствуйте"}},
    }


def test_webhook_needs_the_token(tenants: dict[str, TenantFixture]) -> None:
    body = json.dumps(_incoming("A1")).encode()
    assert Client().post(HOOK, body, content_type="application/json").status_code == 401
    assert _deliver(_incoming("A1"), token="wrong").status_code == 401
    assert InboxMessage.objects.using(ALIAS).count() == 0


def test_incoming_message_creates_chat_and_lead(tenants: dict[str, TenantFixture]) -> None:
    assert _deliver(_incoming("A1")).status_code == 200

    conversation = ConversationRecord.objects.using(ALIAS).get()
    assert (conversation.channel, conversation.external_contact_id) == ("whatsapp", NUMBER)
    lead = LeadRecord.objects.using(ALIAS).get()
    assert (lead.name, lead.phone, lead.source) == ("Aida", f"+{NUMBER}", "whatsapp")
    assert conversation.lead_id == lead.id
    message = MessageRecord.objects.using(ALIAS).get()
    assert (message.text, message.external_id) == ("Здравствуйте", "whatsapp:A1")


def test_retried_webhook_changes_nothing(tenants: dict[str, TenantFixture]) -> None:
    _deliver(_incoming("A1"))
    assert _deliver(_incoming("A1")).json()["duplicate"] is True
    assert MessageRecord.objects.using(ALIAS).count() == 1


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (
            {"typeMessage": "extendedTextMessage", "extendedTextMessageData": {"text": "ссылка"}},
            "ссылка",
        ),
        ({"typeMessage": "quotedMessage", "extendedTextMessageData": {"text": "ответ"}}, "ответ"),
        (
            {"typeMessage": "imageMessage", "fileMessageData": {"caption": "Прайс"}},
            "[Фото] Прайс",
        ),
        ({"typeMessage": "audioMessage", "fileMessageData": {}}, "[Голосовое сообщение]"),
        ({"typeMessage": "contactMessage", "contactMessageData": {}}, "[Контакт]"),
        ({"typeMessage": "somethingNew"}, "[Сообщение не поддерживается]"),
    ],
)
def test_message_types_stay_visible(
    data: dict[str, Any], expected: str, tenants: dict[str, TenantFixture]
) -> None:
    _deliver(_incoming("A1", data))
    assert MessageRecord.objects.using(ALIAS).get().text == expected


def test_groups_and_reactions_are_not_chats(tenants: dict[str, TenantFixture]) -> None:
    _deliver(_incoming("A1", chat="120363369140947676@g.us"))
    _deliver(
        _incoming(
            "A2", {"typeMessage": "reactionMessage", "extendedTextMessageData": {"text": "👍"}}
        )
    )
    assert ConversationRecord.objects.using(ALIAS).count() == 0
    assert LeadRecord.objects.using(ALIAS).count() == 0


def test_contact_without_a_phone_keeps_the_chat_but_gets_no_lead(
    tenants: dict[str, TenantFixture],
) -> None:
    _deliver(_incoming("A1", chat="120650379300963@lid"))
    assert ConversationRecord.objects.using(ALIAS).get().external_contact_id == (
        "120650379300963@lid"
    )
    assert LeadRecord.objects.using(ALIAS).count() == 0


def test_reply_typed_on_the_phone_appears_as_sent(tenants: dict[str, TenantFixture]) -> None:
    _deliver(_incoming("A1"))
    outgoing = {
        **_incoming(
            "B1", {"typeMessage": "textMessage", "textMessageData": {"textMessage": "Ответ"}}
        ),
        "typeWebhook": "outgoingMessageReceived",
    }
    _deliver(outgoing)

    reply = MessageRecord.objects.using(ALIAS).get(direction="outbound")
    assert (reply.status, reply.text) == ("sent", "Ответ")
    assert ConversationRecord.objects.using(ALIAS).get().unread_count == 1


def _answer_send(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response, seen: list[httpx.Request]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    monkeypatch.setattr(
        SENDER,
        lambda base_url, transport=None: httpx.Client(
            base_url=base_url, transport=httpx.MockTransport(handler)
        ),
    )


def _reply(client: APIClient) -> Any:
    conversation_id = ConversationRecord.objects.using(ALIAS).get().id
    return client.post(
        f"/api/v1/communications/conversations/{conversation_id}/messages/",
        {"text": "Спасибо"},
        format="json",
    )


def test_reply_goes_out_and_a_failed_status_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    seen: list[httpx.Request] = []
    _answer_send(monkeypatch, httpx.Response(200, json={"idMessage": "OUT1"}), seen)
    _deliver(_incoming("A1"))
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")

    sent = _reply(client)

    assert sent.status_code == 201 and sent.json()["external_id"] == "whatsapp:OUT1"
    assert seen[0].url.path == f"/waInstance7103/sendMessage/{API_TOKEN}"
    assert json.loads(seen[0].content) == {"chatId": f"{NUMBER}@c.us", "message": "Спасибо"}

    # Statuses reuse the id: "sent" must not make "noAccount" look like a duplicate.
    status = {"typeWebhook": "outgoingMessageStatus", "idMessage": "OUT1", "timestamp": 1}
    _deliver({**status, "status": "sent"})
    _deliver({**status, "status": "noAccount"})

    message = MessageRecord.objects.using(ALIAS).get(direction="outbound")
    assert (message.status, message.failure_code) == ("failed", "noAccount")


def test_reply_to_a_contact_without_a_phone_keeps_its_id(
    monkeypatch: pytest.MonkeyPatch,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    seen: list[httpx.Request] = []
    _answer_send(monkeypatch, httpx.Response(200, json={"idMessage": "OUT1"}), seen)
    _deliver(_incoming("A1", chat="120650379300963@lid"))
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")

    assert _reply(client).status_code == 201
    assert json.loads(seen[0].content)["chatId"] == "120650379300963@lid"


def _green_answers(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    monkeypatch.setattr(
        CONNECTION,
        lambda base_url, transport=None: httpx.Client(
            base_url=base_url, transport=httpx.MockTransport(handler)
        ),
    )


STATUS_URL = "/api/v1/communications/whatsapp/status/"
QR_URL = "/api/v1/communications/whatsapp/qr/"


def test_status_reports_the_connection(
    monkeypatch: pytest.MonkeyPatch,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    _green_answers(
        monkeypatch, lambda _r: httpx.Response(200, json={"stateInstance": "authorized"})
    )
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")

    assert client.get(STATUS_URL).json() == {
        "provider": "green_api",
        "configured": True,
        "connected": True,
        "state": "authorized",
    }


def test_status_survives_green_api_being_down(
    monkeypatch: pytest.MonkeyPatch,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    def down(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    _green_answers(monkeypatch, down)
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")

    body = client.get(STATUS_URL).json()
    assert (body["connected"], body["state"]) == (False, "unavailable")


def test_owner_gets_the_qr_code(
    monkeypatch: pytest.MonkeyPatch,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    _green_answers(
        monkeypatch, lambda _r: httpx.Response(200, json={"type": "qrCode", "message": "UE5H"})
    )
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")

    assert client.get(QR_URL).json() == {
        "status": "qr",
        "image": "data:image/png;base64,UE5H",
    }


def test_qr_is_closed_to_other_roles_and_other_companies(
    monkeypatch: pytest.MonkeyPatch,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    def must_not_be_called(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("Green API must not be called")

    _green_answers(monkeypatch, must_not_be_called)
    viewer = client_for(make_user("viewer", tenant=tenants["acme"], role=Role.VIEWER), "acme")
    other = client_for(make_user("rival", tenant=tenants["globex"]), "globex")

    assert viewer.get(QR_URL).status_code == 403
    assert other.get(QR_URL).json() == {"status": "unavailable", "image": None}
    assert other.get(STATUS_URL).json()["configured"] is False


def test_check_reports_a_number_waiting_for_the_qr_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _green_answers(
        monkeypatch, lambda _r: httpx.Response(200, json={"stateInstance": "notAuthorized"})
    )
    with pytest.raises(CommandError, match="scan the QR code"):
        call_command("whatsapp_check", stdout=StringIO())


def test_check_connects_the_webhook_without_printing_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"saveSettings": True})

    _green_answers(monkeypatch, handler)
    out = StringIO()

    call_command("whatsapp_check", public_url="https://crm.example.com/", stdout=out)

    body = json.loads(seen[0].content)
    assert body["webhookUrl"] == "https://crm.example.com/api/v1/webhooks/green_api/acme/"
    assert body["webhookUrlToken"] == f"Bearer {WEBHOOK_TOKEN}"
    assert (body["incomingWebhook"], body["outgoingMessageWebhook"]) == ("yes", "yes")
    assert API_TOKEN not in out.getvalue() and WEBHOOK_TOKEN not in out.getvalue()
