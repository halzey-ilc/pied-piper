"""Webhook signatures, provider specs and outbound HTTP policy (no network: MockTransport)."""

from __future__ import annotations

import hashlib
import hmac
import json
import random

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from modules.integrations.application.contracts import OutboundMessage
from modules.integrations.domain.providers import Provider
from modules.integrations.infrastructure.adapters import TelegramBotAdapter, WhatsAppCloudAdapter
from modules.integrations.infrastructure.http import build_http_client, send_with_retry
from modules.integrations.infrastructure.providers import build_provider_specs
from modules.integrations.infrastructure.signatures import (
    HmacSha256Verifier,
    SharedSecretHeaderVerifier,
    TimestampedHmacVerifier,
)
from shared.application.retry import PermanentError, RetryPolicy, TransientError

BODY = b'{"entry":[{"changes":[{"value":{"messages":[{"id":"wamid.1"}]}}]}]}'
NO_SLEEP = lambda _s: None  # noqa: E731
FAST = RetryPolicy(
    max_attempts=3, base_delay_seconds=0.01, max_delay_seconds=0.01, rng=random.Random(0)
)


def _meta_sig(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_meta_hmac_signature() -> None:
    verifier = HmacSha256Verifier("app-secret")
    assert verifier.verify({"x-hub-signature-256": _meta_sig("app-secret", BODY)}, BODY)
    assert not verifier.verify({"X-Hub-Signature-256": _meta_sig("other", BODY)}, BODY)
    assert not verifier.verify({"X-Hub-Signature-256": _meta_sig("app-secret", BODY)}, BODY + b" ")
    assert not verifier.verify({}, BODY)


def test_telegram_secret_header() -> None:
    verifier = SharedSecretHeaderVerifier("tg-secret")
    assert verifier.verify({"X-Telegram-Bot-Api-Secret-Token": "tg-secret"}, b"{}")
    assert not verifier.verify({"X-Telegram-Bot-Api-Secret-Token": "nope"}, b"{}")


def test_timestamped_hmac_rejects_replays() -> None:
    now = 1_800_000_000
    verifier = TimestampedHmacVerifier("s3", clock=lambda: float(now))
    ts = str(now)
    good = {
        "X-Signature": TimestampedHmacVerifier.sign("s3", ts, BODY),
        "X-Signature-Timestamp": ts,
    }
    assert verifier.verify(good, BODY)
    old = str(now - 3600)
    stale = {
        "X-Signature": TimestampedHmacVerifier.sign("s3", old, BODY),
        "X-Signature-Timestamp": old,
    }
    assert not verifier.verify(stale, BODY)


def test_provider_specs_unconfigured_and_event_ids() -> None:
    specs = build_provider_specs({"whatsapp": SecretStr("x"), "telegram": None})
    assert specs[Provider.TELEGRAM].verifier() is None
    assert specs[Provider.WHATSAPP].verifier() is not None
    assert specs[Provider.WHATSAPP].event_id(json.loads(BODY), BODY) == "wamid.1"
    assert specs[Provider.TELEGRAM].event_id({"update_id": 7}, b"") == "update:7"
    assert specs[Provider.TELEPHONY].event_id({}, b"abc").startswith("sha256:")


def test_send_with_retry_retries_transient_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, json={"ok": True})

    client = build_http_client("https://provider.invalid", transport=httpx.MockTransport(handler))
    response = send_with_retry(lambda: client.get("/x"), FAST, sleep=NO_SLEEP)
    assert response.status_code == 200 and calls["n"] == 3


def test_send_with_retry_gives_up_and_classifies() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client = build_http_client("https://provider.invalid", transport=httpx.MockTransport(timeout))
    with pytest.raises(TransientError):
        send_with_retry(lambda: client.get("/x"), FAST, sleep=NO_SLEEP)

    bad = build_http_client(
        "https://provider.invalid", transport=httpx.MockTransport(lambda r: httpx.Response(400))
    )
    with pytest.raises(PermanentError):
        send_with_retry(lambda: bad.get("/x"), FAST, sleep=NO_SLEEP)


def test_http_client_has_explicit_timeouts() -> None:
    client = build_http_client("https://provider.invalid", connect_timeout=1.5, read_timeout=7)
    assert client.timeout.connect == 1.5 and client.timeout.read == 7
    assert client.follow_redirects is False


def test_channel_adapters_build_requests_without_network() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "sendMessage" in request.url.path:
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})
        return httpx.Response(200, json={"messages": [{"id": "wamid.9"}]})

    transport = httpx.MockTransport(handler)
    msg = OutboundMessage(
        provider=Provider.WHATSAPP,
        recipient_external_id="77011234567",
        text="hi",
        idempotency_key="key-12345",
    )
    wa = WhatsAppCloudAdapter(
        build_http_client("https://graph.invalid", transport=transport), "123", "tok", FAST
    )
    assert wa.send(msg).external_message_id == "wamid.9"
    tg = TelegramBotAdapter(
        build_http_client("https://tg.invalid", transport=transport), "bot-tok", FAST
    )
    assert (
        tg.send(msg.model_copy(update={"provider": Provider.TELEGRAM})).external_message_id == "5"
    )
    assert seen[0].headers["Authorization"] == "Bearer tok"


def test_outbound_contract_validation() -> None:
    with pytest.raises(ValidationError):
        OutboundMessage(
            provider=Provider.TELEGRAM, recipient_external_id="1", text="", idempotency_key="k"
        )
