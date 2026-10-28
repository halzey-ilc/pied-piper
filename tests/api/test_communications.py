"""Communications: webhook -> conversation path, messaging API, RBAC, isolation."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from django.test import Client
from pydantic import SecretStr
from rest_framework.test import APIClient

from entrypoints.workers.tasks.integrations import process_webhook
from modules.access_control.domain.rbac import Role
from modules.communications.application.service import ConversationService, InboundMessage
from modules.communications.composition import conversation_service
from modules.communications.domain.conversation import Channel
from modules.communications.infrastructure.models import ConversationRecord, MessageRecord
from platform_core.infrastructure.messaging.models import InboxMessage
from platform_core.infrastructure.tenancy.services import system_tenant_scope
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)
ALIAS = "tenant_acme"
TG_SECRET = "telegram-webhook-secret-for-tests"


def _tg_update(message_id: int, text: str = "Hello") -> dict[str, Any]:
    return {
        "update_id": message_id,
        "message": {
            "message_id": message_id,
            "date": 1793000000,
            "from": {"id": 42, "first_name": "Jane"},
            "text": text,
        },
    }


def test_inbound_webhook_creates_conversation(
    make_user: Callable[..., Any], tenants: dict[str, TenantFixture], settings: Any
) -> None:
    settings.INTEGRATIONS = {
        **settings.INTEGRATIONS,
        "SECRETS": {**settings.INTEGRATIONS["SECRETS"], "telegram": SecretStr(TG_SECRET)},
    }
    body = json.dumps(_tg_update(1)).encode()
    response = Client().post(
        "/api/v1/webhooks/telegram/acme/",
        body,
        content_type="application/json",
        headers={"X-Telegram-Bot-Api-Secret-Token": TG_SECRET},
    )
    assert response.status_code == 200
    inbox_id = str(InboxMessage.objects.using(ALIAS).get().id)

    # The worker processes the stored webhook into a conversation.
    process_webhook("acme", "telegram", inbox_id)

    conversation = ConversationRecord.objects.using(ALIAS).get()
    assert conversation.channel == "telegram" and conversation.external_contact_id == "42"
    assert conversation.unread_count == 1 and conversation.owner_user_id is None
    message = MessageRecord.objects.using(ALIAS).get()
    assert message.direction == "inbound" and message.text == "Hello"


def test_ingest_is_idempotent(tenants: dict[str, TenantFixture]) -> None:
    inbound = InboundMessage(
        channel=Channel.TELEGRAM,
        external_contact_id="42",
        external_message_id="tg:1",
        text="hi",
        occurred_at=datetime.now(UTC),
    )
    with system_tenant_scope("acme"):
        service: ConversationService = conversation_service()
        assert service.ingest_inbound(inbound) is True
        assert service.ingest_inbound(inbound) is False  # same provider message id
    assert MessageRecord.objects.using(ALIAS).count() == 1
    assert ConversationRecord.objects.using(ALIAS).count() == 1


def test_messaging_api_assign_send_close(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    # Seed an unassigned conversation via inbound ingest.
    with system_tenant_scope("acme"):
        conversation_service().ingest_inbound(
            InboundMessage(
                channel=Channel.TELEGRAM,
                external_contact_id="42",
                external_message_id="tg:1",
                text="hi",
                occurred_at=datetime.now(UTC),
            )
        )
    conv_id = str(ConversationRecord.objects.using(ALIAS).get().id)
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")

    assert client.get("/api/v1/communications/conversations/").json()["count"] == 1
    assigned = client.post(
        f"/api/v1/communications/conversations/{conv_id}/assign/", {}, format="json"
    )
    assert assigned.json()["owner_user_id"] is not None

    sent = client.post(
        f"/api/v1/communications/conversations/{conv_id}/messages/",
        {"text": "Thanks for reaching out"},
        format="json",
    )
    assert sent.status_code == 201 and sent.json()["direction"] == "outbound"
    assert sent.json()["status"] == "sent" and sent.json()["external_id"].startswith("stub-")

    messages = client.get(f"/api/v1/communications/conversations/{conv_id}/messages/").json()
    assert len(messages["results"]) == 2  # inbound + outbound

    closed = client.post(
        f"/api/v1/communications/conversations/{conv_id}/close/", {}, format="json"
    )
    assert closed.json()["status"] == "closed"
    # Cannot send to a closed conversation.
    assert (
        client.post(
            f"/api/v1/communications/conversations/{conv_id}/messages/",
            {"text": "x"},
            format="json",
        ).status_code
        == 409
    )


def test_rbac_and_isolation(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    with system_tenant_scope("acme"):
        conversation_service().ingest_inbound(
            InboundMessage(
                channel=Channel.TELEGRAM,
                external_contact_id="42",
                external_message_id="tg:1",
                text="hi",
                occurred_at=datetime.now(UTC),
            )
        )
    conv_id = str(ConversationRecord.objects.using(ALIAS).get().id)
    viewer = client_for(make_user("viewer", tenant=tenants["acme"], role=Role.VIEWER), "acme")
    # Viewers can see (unassigned visible) but not send.
    assert viewer.get("/api/v1/communications/conversations/").json()["count"] == 1
    assert (
        viewer.post(
            f"/api/v1/communications/conversations/{conv_id}/messages/",
            {"text": "x"},
            format="json",
        ).status_code
        == 403
    )

    alice = make_user("alice", tenant=tenants["acme"])
    assert (
        client_for(alice, "globex").get("/api/v1/communications/conversations/").status_code == 404
    )
    assert ConversationRecord.objects.using("tenant_globex").count() == 0
