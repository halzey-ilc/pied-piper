from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from modules.communications.domain.conversation import (
    Channel,
    Conversation,
    ConversationStateError,
    MessageReceived,
    MessageSent,
)
from modules.communications.infrastructure.parsers import parse_inbound
from modules.integrations.domain.providers import Provider
from shared.domain.errors import DomainValidationError

WHEN = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)


def _conv() -> Conversation:
    return Conversation.start(
        channel=Channel.TELEGRAM, external_contact_id="12345", contact_name="Jane", created_at=WHEN
    )


def test_receive_increments_unread_and_emits_event() -> None:
    conv = _conv()
    message, event = conv.receive(text="  hi there ", external_id="tg:1", occurred_at=WHEN)
    assert message.direction.value == "inbound" and message.text == "hi there"
    assert conv.unread_count == 1 and conv.last_message_at == WHEN
    assert isinstance(event, MessageReceived)


def test_send_requires_open_and_emits_event() -> None:
    conv = _conv()
    message, event = conv.send(text="reply", sender_user_id=uuid4(), occurred_at=WHEN)
    assert message.direction.value == "outbound" and message.status.value == "pending"
    assert isinstance(event, MessageSent)
    conv.close()
    with pytest.raises(ConversationStateError):
        conv.send(text="x", sender_user_id=uuid4(), occurred_at=WHEN)


def test_empty_text_rejected() -> None:
    with pytest.raises(DomainValidationError):
        _conv().receive(text="   ", external_id=None, occurred_at=WHEN)


def test_mark_read_and_close_reopen() -> None:
    conv = _conv()
    conv.receive(text="hi", external_id="tg:1", occurred_at=WHEN)
    assert conv.mark_read() is True and conv.unread_count == 0
    assert conv.mark_read() is False
    conv.close()
    with pytest.raises(ConversationStateError):
        conv.close()
    conv.reopen()
    assert conv.status.value == "open"


def test_telegram_parser() -> None:
    payload = {
        "update_id": 1,
        "message": {
            "message_id": 7,
            "date": 1793000000,
            "from": {"id": 42, "first_name": "Jane", "last_name": "Buyer"},
            "text": "Hello",
        },
    }
    inbound = parse_inbound(Provider.TELEGRAM, payload)
    assert inbound is not None
    assert inbound.channel is Channel.TELEGRAM
    assert inbound.external_contact_id == "42"
    assert inbound.external_message_id == "tg:7"
    assert inbound.text == "Hello" and inbound.contact_name == "Jane Buyer"


def test_telegram_parser_ignores_non_text() -> None:
    assert parse_inbound(Provider.TELEGRAM, {"update_id": 1}) is None
    assert parse_inbound(Provider.TELEGRAM, {"message": {"from": {"id": 1}}}) is None


def test_whatsapp_parser() -> None:
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"profile": {"name": "Bob"}}],
                            "messages": [
                                {"id": "wamid.9", "from": "77011112233", "text": {"body": "Hi WA"}}
                            ],
                        }
                    }
                ]
            }
        ]
    }
    inbound = parse_inbound(Provider.WHATSAPP, payload)
    assert inbound is not None
    assert inbound.channel is Channel.WHATSAPP and inbound.external_contact_id == "77011112233"
    assert inbound.external_message_id == "whatsapp:wamid.9" and inbound.text == "Hi WA"
