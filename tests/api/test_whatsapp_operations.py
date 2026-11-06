"""WhatsApp in operation: no fake sends, failure codes, lead filter, setup check."""

from __future__ import annotations

from collections.abc import Callable
from io import StringIO
from typing import Any

import httpx
import pytest
from django.core.management import CommandError, call_command
from pydantic import SecretStr
from rest_framework.test import APIClient

from modules.communications.infrastructure.models import ConversationRecord, MessageRecord
from modules.leads.infrastructure.models import LeadRecord
from tests.api.test_whatsapp import (
    ALIAS,
    _configure,
    _deliver,
    _ingest,
    _message,
    _reply,
    _use_cloud_api,
    _webhook,
)
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)
OWNER = "11111111-1111-4111-8111-111111111111"
CHECK_CLIENT = (
    "modules.communications.infrastructure.management.commands.whatsapp_check.build_http_client"
)


def test_production_refuses_to_pretend_a_message_was_sent(
    settings: Any,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    _configure(settings, ACCESS_TOKEN=None, PHONE_NUMBER_ID=None)
    settings.INTEGRATIONS = {**settings.INTEGRATIONS, "ALLOW_STUB_SENDER": False}
    _ingest(_webhook(messages=[_message("wamid.1")]))
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")

    response = _reply(client)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "channel_not_configured"
    assert not MessageRecord.objects.using(ALIAS).filter(direction="outbound").exists()


def test_failure_code_reaches_the_api(
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
    assert _reply(client).json()["failure_code"] is None

    _deliver(
        _webhook(statuses=[{"id": "wamid.out", "status": "failed", "errors": [{"code": 131047}]}])
    )

    conversation_id = ConversationRecord.objects.using(ALIAS).get().id
    messages = client.get(
        f"/api/v1/communications/conversations/{conversation_id}/messages/"
    ).json()["results"]
    sent = next(m for m in messages if m["direction"] == "outbound")
    assert (sent["status"], sent["failure_code"]) == ("failed", "131047")


def test_conversations_filter_by_lead(
    settings: Any,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    _configure(settings, LEAD_OWNER_USER_ID=OWNER)
    _ingest(_webhook(messages=[_message("wamid.1", sender="996700000001")]))
    _ingest(_webhook(messages=[_message("wamid.2", sender="996700000002")]))
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    lead = LeadRecord.objects.using(ALIAS).get(phone="+996700000002")
    url = "/api/v1/communications/conversations/"

    filtered = client.get(url, {"lead_id": str(lead.id)}).json()

    assert filtered["count"] == 1
    assert filtered["results"][0]["external_contact_id"] == "996700000002"
    assert client.get(url).json()["count"] == 2
    assert client.get(url, {"lead_id": "not-a-uuid"}).status_code == 400


def _ready(settings: Any) -> None:
    _configure(
        settings,
        VERIFY_TOKEN=SecretStr("verify"),
        ACCESS_TOKEN=SecretStr("super-secret-token"),
        PHONE_NUMBER_ID="123",
        LEAD_OWNER_USER_ID=OWNER,
    )


def _meta_answers(monkeypatch: pytest.MonkeyPatch, response: httpx.Response) -> None:
    monkeypatch.setattr(
        CHECK_CLIENT,
        lambda base_url: httpx.Client(
            base_url=base_url, transport=httpx.MockTransport(lambda _request: response)
        ),
    )


def test_check_confirms_the_number_without_printing_secrets(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ready(settings)
    phone = {"display_phone_number": "+996 708 026 052", "verified_name": "Demo"}
    _meta_answers(monkeypatch, httpx.Response(200, json=phone))
    out = StringIO()

    call_command("whatsapp_check", stdout=out)

    assert "+996 708 026 052" in out.getvalue()
    assert "super-secret-token" not in out.getvalue()


def test_check_lists_missing_settings(settings: Any) -> None:
    _configure(settings, ACCESS_TOKEN=None, PHONE_NUMBER_ID="123")
    with pytest.raises(CommandError, match="WHATSAPP_ACCESS_TOKEN"):
        call_command("whatsapp_check", stdout=StringIO())


def test_check_fails_when_meta_refuses_the_token(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ready(settings)
    _meta_answers(monkeypatch, httpx.Response(401, json={"error": {"code": 190}}))
    with pytest.raises(CommandError, match="HTTP 401"):
        call_command("whatsapp_check", stdout=StringIO())
