"""Webhook endpoint: signature enforcement, deduplication and tenant scoping."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest
from django.test import Client
from pydantic import SecretStr

from platform_core.infrastructure.messaging.models import InboxMessage
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)

SECRET = "whatsapp-app-secret-for-tests"
BODY = json.dumps(
    {"entry": [{"changes": [{"value": {"messages": [{"id": "wamid.42"}]}}]}]}
).encode()


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def configured(settings: Any) -> None:
    settings.INTEGRATIONS = {
        **settings.INTEGRATIONS,
        "SECRETS": {**settings.INTEGRATIONS["SECRETS"], "whatsapp": SecretStr(SECRET)},
    }


def _post(slug: str, body: bytes = BODY, signature: str | None = None) -> Any:
    headers = {"X-Hub-Signature-256": signature} if signature else None
    return Client().post(
        f"/api/v1/webhooks/whatsapp/{slug}/", body, content_type="application/json", headers=headers
    )


def test_unconfigured_integration_is_rejected(tenants: dict[str, TenantFixture]) -> None:
    assert _post("acme", signature=_sign(BODY)).status_code == 503
    assert InboxMessage.objects.using("tenant_acme").count() == 0


@pytest.mark.usefixtures("configured")
def test_bad_or_missing_signature_is_rejected(tenants: dict[str, TenantFixture]) -> None:
    assert _post("acme").status_code == 401
    assert _post("acme", signature=_sign(BODY, "wrong")).status_code == 401
    assert _post("acme", body=BODY + b" ", signature=_sign(BODY)).status_code == 401
    assert InboxMessage.objects.using("tenant_acme").count() == 0


@pytest.mark.usefixtures("configured")
def test_valid_webhook_is_stored_once_in_the_right_tenant(
    tenants: dict[str, TenantFixture], django_capture_on_commit_callbacks: Any
) -> None:
    first = _post("acme", signature=_sign(BODY))
    second = _post("acme", signature=_sign(BODY))  # provider retry
    assert first.status_code == 200 and first.json()["duplicate"] is False
    assert second.status_code == 200 and second.json()["duplicate"] is True
    row = InboxMessage.objects.using("tenant_acme").get()
    assert (row.source, row.message_id) == ("webhook.whatsapp", "wamid.42")
    assert InboxMessage.objects.using("tenant_globex").count() == 0


@pytest.mark.usefixtures("configured")
def test_unknown_provider_or_tenant(tenants: dict[str, TenantFixture]) -> None:
    assert (
        Client()
        .post("/api/v1/webhooks/fax/acme/", BODY, content_type="application/json")
        .status_code
        == 404
    )
    assert _post("nobody", signature=_sign(BODY)).status_code == 404


@pytest.mark.usefixtures("configured")
def test_oversized_payload_rejected(tenants: dict[str, TenantFixture], settings: Any) -> None:
    settings.INTEGRATIONS = {**settings.INTEGRATIONS, "WEBHOOK_MAX_BODY_BYTES": 10}
    assert _post("acme", signature=_sign(BODY)).status_code == 413
