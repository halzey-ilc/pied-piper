"""WhatsApp Cloud API: webhook parsing, event ids and the outbound sender."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from modules.communications.domain.conversation import Channel
from modules.communications.infrastructure.parsers import (
    parse_delivery_failures,
    parse_messages,
)
from modules.communications.infrastructure.sender import (
    ChannelDeliveryError,
    StubSender,
    WhatsAppCloudSender,
)
from modules.integrations.domain.providers import Provider
from modules.integrations.infrastructure.providers import body_hash, build_provider_specs


def _webhook(**value: Any) -> dict[str, Any]:
    return {"entry": [{"changes": [{"field": "messages", "value": value}]}]}


def _text(message_id: str, sender: str = "996700000001", body: str = "Hi") -> dict[str, Any]:
    return {
        "id": message_id,
        "from": sender,
        "timestamp": "1793000000",
        "type": "text",
        "text": {"body": body},
    }


def test_every_message_in_a_batch_is_parsed_with_its_own_name() -> None:
    payload = _webhook(
        contacts=[
            {"wa_id": "996700000001", "profile": {"name": "Aida"}},
            {"wa_id": "996700000002", "profile": {"name": "Bek"}},
        ],
        messages=[_text("wamid.1"), _text("wamid.2", sender="996700000002")],
    )
    first, second = parse_messages(Provider.WHATSAPP, payload)
    assert (first.contact_name, second.contact_name) == ("Aida", "Bek")
    assert second.external_message_id == "whatsapp:wamid.2"
    assert first.occurred_at == datetime.fromtimestamp(1793000000, tz=UTC)
    assert first.from_business is False


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ({"type": "image", "image": {"id": "m1"}}, "[Фото]"),
        ({"type": "image", "image": {"caption": "Прайс"}}, "[Фото] Прайс"),
        ({"type": "audio", "audio": {"id": "m1"}}, "[Голосовое сообщение]"),
        ({"type": "button", "button": {"text": "Да"}}, "Да"),
        (
            {"type": "interactive", "interactive": {"button_reply": {"title": "Записаться"}}},
            "Записаться",
        ),
        ({"type": "unknown"}, "[Сообщение не поддерживается]"),
    ],
)
def test_non_text_messages_stay_visible(message: dict[str, Any], expected: str) -> None:
    payload = _webhook(messages=[{"id": "wamid.1", "from": "996700000001", **message}])
    assert parse_messages(Provider.WHATSAPP, payload)[0].text == expected


def test_reactions_and_status_only_payloads_are_not_messages() -> None:
    reaction = {"id": "wamid.1", "from": "996700000001", "type": "reaction", "reaction": {}}
    assert parse_messages(Provider.WHATSAPP, _webhook(messages=[reaction])) == []
    assert parse_messages(Provider.WHATSAPP, _webhook(statuses=[{"status": "read"}])) == []
    assert parse_messages(Provider.WHATSAPP, {"entry": "garbage"}) == []


def test_phone_app_echo_is_addressed_to_the_customer() -> None:
    echo = {
        "id": "wamid.9",
        "from": "996708026052",
        "to": "996700000001",
        "type": "text",
        "text": {"body": "Reply from the phone"},
    }
    (parsed,) = parse_messages(Provider.WHATSAPP, _webhook(message_echoes=[echo]))
    assert parsed.from_business is True
    assert parsed.external_contact_id == "996700000001"


def test_only_failed_statuses_are_delivery_failures() -> None:
    payload = _webhook(
        statuses=[
            {"id": "wamid.1", "status": "delivered"},
            {"id": "wamid.2", "status": "failed", "errors": [{"code": 131047}]},
        ]
    )
    (failure,) = parse_delivery_failures(Provider.WHATSAPP, payload)
    assert failure.external_message_id == "whatsapp:wamid.2" and failure.code == "131047"
    assert parse_delivery_failures(Provider.TELEGRAM, payload) == []


def test_event_id_uses_the_message_id_only_for_a_single_message() -> None:
    spec = build_provider_specs({})[Provider.WHATSAPP]
    single = _webhook(messages=[_text("wamid.1")])
    batch = _webhook(messages=[_text("wamid.1"), _text("wamid.2")])
    assert spec.event_id(single, b"single") == "wamid.1"
    assert spec.event_id(batch, b"batch") == body_hash(b"batch")
    assert spec.event_id(_webhook(statuses=[{}]), b"status") == body_hash(b"status")


def _sender(handler: Any) -> WhatsAppCloudSender:
    return WhatsAppCloudSender(
        base_url="https://graph.example/v23.0",
        phone_number_id="123",
        access_token="token",
        fallback=StubSender(),
        transport=httpx.MockTransport(handler),
    )


def test_sender_posts_to_the_cloud_api_and_namespaces_the_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.5"}]})

    sent = _sender(handler).send(channel=Channel.WHATSAPP, to_contact="996700000001", text="Hi")

    assert sent == "whatsapp:wamid.5"
    assert str(seen[0].url) == "https://graph.example/v23.0/123/messages"
    assert seen[0].headers["Authorization"] == "Bearer token"
    assert json.loads(seen[0].content)["to"] == "996700000001"


def test_sender_reports_a_rejected_message() -> None:
    sender = _sender(lambda _request: httpx.Response(400, json={"error": {"code": 100}}))
    with pytest.raises(ChannelDeliveryError):
        sender.send(channel=Channel.WHATSAPP, to_contact="996700000001", text="Hi")


def test_other_channels_use_the_fallback() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call the WhatsApp API")

    sent = _sender(handler).send(channel=Channel.TELEGRAM, to_contact="42", text="Hi")
    assert sent.startswith("stub-telegram-")
