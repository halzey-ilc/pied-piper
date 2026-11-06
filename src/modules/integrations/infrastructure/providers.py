"""Provider specifications: signature scheme + event-id extraction per provider.

Event ids are used to deduplicate provider retries. Where a payload has no
stable id, the SHA-256 of the raw body is used.

These are extension points only. No real accounts are connected and the payload
shapes have not been verified against live provider APIs.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr

from modules.integrations.application.ports import WebhookVerifier
from modules.integrations.domain.providers import Provider
from modules.integrations.infrastructure.signatures import (
    BearerTokenVerifier,
    HmacSha256Verifier,
    SharedSecretHeaderVerifier,
    TimestampedHmacVerifier,
)


def body_hash(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _meta_event_id(payload: Mapping[str, Any], body: bytes) -> str:
    # Meta batches changes in entry[].changes[]. A lone message keeps its own id;
    # a batch hashes the body, so one sharing a first message is not dropped as a
    # duplicate. Retries repeat the body, and ingest is idempotent per message.
    try:
        ids = [
            message["id"]
            for entry in payload["entry"]
            for change in entry["changes"]
            for key in ("messages", "message_echoes")
            for message in change["value"].get(key) or []
        ]
    except (KeyError, TypeError, AttributeError):
        return body_hash(body)
    return str(ids[0]) if len(ids) == 1 else body_hash(body)


def _green_api_event_id(payload: Mapping[str, Any], body: bytes) -> str:
    # One idMessage is shared by a message and each of its statuses.
    message_id = payload.get("idMessage")
    if not isinstance(message_id, str) or not message_id:
        return body_hash(body)
    return f"{payload.get('typeWebhook')}:{message_id}:{payload.get('status', '')}"


def _telegram_event_id(payload: Mapping[str, Any], body: bytes) -> str:
    update_id = payload.get("update_id")
    return f"update:{update_id}" if isinstance(update_id, int) else body_hash(body)


def _generic_event_id(payload: Mapping[str, Any], body: bytes) -> str:
    event_id = payload.get("event_id") or payload.get("id")
    return str(event_id) if isinstance(event_id, str | int) and str(event_id) else body_hash(body)


@dataclass(frozen=True)
class ConfiguredProviderSpec:
    provider: Provider
    secret: SecretStr | None
    verifier_factory: Callable[[str], WebhookVerifier]
    event_id_extractor: Callable[[Mapping[str, Any], bytes], str]

    def verifier(self) -> WebhookVerifier | None:
        if self.secret is None:
            return None
        return self.verifier_factory(self.secret.get_secret_value())

    def event_id(self, payload: Mapping[str, Any], body: bytes) -> str:
        return self.event_id_extractor(payload, body)[:255]


def build_provider_specs(
    secrets: Mapping[str, SecretStr | None],
) -> dict[Provider, ConfiguredProviderSpec]:
    return {
        Provider.WHATSAPP: ConfiguredProviderSpec(
            Provider.WHATSAPP, secrets.get("whatsapp"), HmacSha256Verifier, _meta_event_id
        ),
        Provider.GREEN_API: ConfiguredProviderSpec(
            Provider.GREEN_API, secrets.get("green_api"), BearerTokenVerifier, _green_api_event_id
        ),
        Provider.INSTAGRAM: ConfiguredProviderSpec(
            Provider.INSTAGRAM, secrets.get("instagram"), HmacSha256Verifier, _meta_event_id
        ),
        Provider.TELEGRAM: ConfiguredProviderSpec(
            Provider.TELEGRAM,
            secrets.get("telegram"),
            SharedSecretHeaderVerifier,
            _telegram_event_id,
        ),
        Provider.TELEPHONY: ConfiguredProviderSpec(
            Provider.TELEPHONY, secrets.get("telephony"), TimestampedHmacVerifier, _generic_event_id
        ),
    }
