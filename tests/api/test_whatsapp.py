"""WhatsApp end to end: webhook verification, leads, phone echoes and delivery."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from django.test import Client
from pydantic import SecretStr
from rest_framework.test import APIClient

from entrypoints.workers.tasks.integrations import process_webhook
from modules.access_control.domain.rbac import Role
from modules.communications.composition import conversation_service
from modules.communications.infrastructure.models import ConversationRecord, MessageRecord
from modules.communications.infrastructure.parsers import parse_messages
from modules.customers.infrastructure.models import CustomerRecord
from modules.integrations.domain.providers import Provider
from modules.leads.infrastructure.models import LeadRecord
from platform_core.infrastructure.messaging.models import InboxMessage
from platform_core.infrastructure.tenancy.services import system_tenant_scope
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)
ALIAS = "tenant_acme"
SECRET = "whatsapp-app-secret-for-tests"
VERIFY_TOKEN = "verify-token-for-tests"
PHONE = "996700000001"
URL = "/api/v1/webhooks/whatsapp/acme/"


def _configure(settings: Any, **whatsapp: Any) -> None:
    settings.INTEGRATIONS = {
        **settings.INTEGRATIONS,
        "SECRETS": {**settings.INTEGRATIONS["SECRETS"], "whatsapp": SecretStr(SECRET)},
        "WHATSAPP": {**settings.INTEGRATIONS["WHATSAPP"], **whatsapp},
    }


@pytest.fixture
def lead_owner(settings: Any) -> UUID:
    owner = uuid4()
    _configure(settings, VERIFY_TOKEN=SecretStr(VERIFY_TOKEN), LEAD_OWNER_USER_ID=owner)
    return owner


def _message(message_id: str, sender: str = PHONE) -> dict[str, Any]:
    return {"id": message_id, "from": sender, "type": "text", "text": {"body": "Здравствуйте"}}


def _webhook(**value: Any) -> dict[str, Any]:
    return {"entry": [{"changes": [{"value": value}]}]}


def _ingest(payload: dict[str, Any]) -> None:
    with system_tenant_scope("acme"):
        service = conversation_service()
        for inbound in parse_messages(Provider.WHATSAPP, payload):
            service.ingest_inbound(inbound)


def _deliver(payload: dict[str, Any]) -> None:
    """The real path: signed webhook, inbox, then the worker task."""
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    response = Client().post(
        URL, body, content_type="application/json", headers={"X-Hub-Signature-256": signature}
    )
    assert response.status_code == 200
    inbox = InboxMessage.objects.using(ALIAS).order_by("-received_at").first()
    assert inbox is not None
    process_webhook("acme", "whatsapp", str(inbox.id))


def _verify(token: str, slug: str = "acme", provider: str = "whatsapp") -> Any:
    return Client().get(
        f"/api/v1/webhooks/{provider}/{slug}/",
        {"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "1158201444"},
    )


def test_channels_report_whatsapp_only_once_its_account_is_configured(
    settings: Any,
    tenants: dict[str, TenantFixture],
    make_user: Callable[..., Any],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("dir", tenant=tenants["acme"], role=Role.DIRECTOR), "acme")

    def can_send() -> dict[str, bool]:
        results = client.get("/api/v1/communications/channels/").json()["results"]
        return {row["channel"]: row["can_send"] for row in results}

    assert not any(can_send().values())
    _configure(settings, ACCESS_TOKEN=SecretStr("token"), PHONE_NUMBER_ID="123")
    assert can_send() == {"whatsapp": True, "telegram": False, "instagram": False, "email": False}


@pytest.mark.usefixtures("lead_owner")
def test_verification_echoes_the_challenge_as_plain_text(
    tenants: dict[str, TenantFixture],
) -> None:
    response = _verify(VERIFY_TOKEN)
    assert response.status_code == 200 and response.content == b"1158201444"
    assert response["Content-Type"].startswith("text/plain")


@pytest.mark.usefixtures("lead_owner")
def test_verification_rejects_a_wrong_token_tenant_or_provider(
    tenants: dict[str, TenantFixture],
) -> None:
    assert _verify("wrong").status_code == 403
    assert _verify(VERIFY_TOKEN, slug="nobody").status_code == 404
    assert _verify(VERIFY_TOKEN, provider="telegram").status_code == 404


def test_verification_is_off_until_a_token_is_set(tenants: dict[str, TenantFixture]) -> None:
    assert _verify("").status_code == 404


def test_batch_webhook_reaches_the_crm_and_creates_one_lead_per_number(
    lead_owner: UUID, tenants: dict[str, TenantFixture]
) -> None:
    _deliver(
        _webhook(
            contacts=[{"wa_id": PHONE, "profile": {"name": "Aida"}}],
            messages=[_message("wamid.1"), _message("wamid.2")],
        )
    )

    assert MessageRecord.objects.using(ALIAS).count() == 2
    lead = LeadRecord.objects.using(ALIAS).get()
    assert (lead.name, lead.phone, lead.source) == ("Aida", f"+{PHONE}", "whatsapp")
    assert lead.owner_user_id == lead_owner
    conversation = ConversationRecord.objects.using(ALIAS).get()
    assert conversation.lead_id == lead.id and conversation.unread_count == 2


@pytest.mark.usefixtures("lead_owner")
def test_known_numbers_link_instead_of_creating_a_duplicate(
    tenants: dict[str, TenantFixture],
) -> None:
    lead = LeadRecord.objects.using(ALIAS).create(
        id=uuid4(),
        name="Existing",
        phone=f"+{PHONE}",
        source="website",
        owner_user_id=uuid4(),
        created_at=datetime.now(UTC),
    )

    _ingest(_webhook(messages=[_message("wamid.1")]))

    assert LeadRecord.objects.using(ALIAS).count() == 1
    assert ConversationRecord.objects.using(ALIAS).get().lead_id == lead.id


def test_message_is_kept_when_no_lead_owner_is_configured(
    settings: Any, tenants: dict[str, TenantFixture]
) -> None:
    _configure(settings, LEAD_OWNER_USER_ID=None)

    _ingest(_webhook(messages=[_message("wamid.1")]))

    assert MessageRecord.objects.using(ALIAS).count() == 1
    assert LeadRecord.objects.using(ALIAS).count() == 0
    assert CustomerRecord.objects.using(ALIAS).count() == 0
    assert ConversationRecord.objects.using(ALIAS).get().lead_id is None


@pytest.mark.usefixtures("lead_owner")
def test_message_is_kept_when_the_number_is_not_a_valid_phone(
    tenants: dict[str, TenantFixture],
) -> None:
    _ingest(_webhook(messages=[_message("wamid.1", sender="not-a-phone")]))

    assert MessageRecord.objects.using(ALIAS).count() == 1
    assert LeadRecord.objects.using(ALIAS).count() == 0


@pytest.mark.usefixtures("lead_owner")
def test_reply_from_the_phone_shows_as_sent_without_touching_unread(
    tenants: dict[str, TenantFixture],
) -> None:
    _ingest(_webhook(messages=[_message("wamid.1")]))
    echo = {"id": "wamid.2", "to": PHONE, "type": "text", "text": {"body": "Добрый день"}}

    _ingest(_webhook(message_echoes=[echo]))
    _ingest(_webhook(message_echoes=[echo]))  # provider retry

    reply = MessageRecord.objects.using(ALIAS).get(direction="outbound")
    assert reply.status == "sent" and reply.text == "Добрый день"
    assert ConversationRecord.objects.using(ALIAS).get().unread_count == 1


def _use_cloud_api(
    settings: Any, monkeypatch: pytest.MonkeyPatch, response: httpx.Response
) -> None:
    _configure(settings, ACCESS_TOKEN=SecretStr("token"), PHONE_NUMBER_ID="123")
    monkeypatch.setattr(
        "modules.communications.infrastructure.sender.build_http_client",
        lambda base_url, transport=None: httpx.Client(
            base_url=base_url, transport=httpx.MockTransport(lambda _request: response)
        ),
    )


def _reply(client: APIClient) -> Any:
    conversation_id = ConversationRecord.objects.using(ALIAS).get().id
    return client.post(
        f"/api/v1/communications/conversations/{conversation_id}/messages/",
        {"text": "Спасибо за обращение"},
        format="json",
    )


@pytest.mark.usefixtures("lead_owner")
def test_failed_status_marks_the_sent_message_failed(
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    _use_cloud_api(
        settings, monkeypatch, httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})
    )
    _ingest(_webhook(messages=[_message("wamid.1")]))
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")

    sent = _reply(client)
    assert sent.status_code == 201 and sent.json()["external_id"] == "whatsapp:wamid.out"

    _deliver(
        _webhook(statuses=[{"id": "wamid.out", "status": "failed", "errors": [{"code": 131047}]}])
    )
    assert MessageRecord.objects.using(ALIAS).get(direction="outbound").status == "failed"


@pytest.mark.usefixtures("lead_owner")
def test_rejected_send_is_an_error_and_stores_nothing(
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    _use_cloud_api(settings, monkeypatch, httpx.Response(400, json={"error": {"code": 100}}))
    _ingest(_webhook(messages=[_message("wamid.1")]))
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")

    assert _reply(client).status_code == 409
    assert not MessageRecord.objects.using(ALIAS).filter(direction="outbound").exists()
