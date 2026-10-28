"""Parse a provider webhook payload into a normalized inbound message.

Shapes follow the providers' public webhook formats but are NOT verified against
live accounts. Returns None when the payload carries no text message (e.g. a
status update), so it is simply ignored.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from modules.communications.application.service import InboundMessage
from modules.communications.domain.conversation import Channel
from modules.integrations.domain.providers import Provider


def _telegram(payload: dict[str, Any]) -> InboundMessage | None:
    message = payload.get("message")
    if not isinstance(message, dict):
        return None
    text = message.get("text")
    sender = message.get("from") or {}
    if not isinstance(text, str) or not text or "id" not in sender:
        return None
    name = " ".join(filter(None, [sender.get("first_name"), sender.get("last_name")])) or None
    occurred = message.get("date")
    occurred_at = (
        datetime.fromtimestamp(occurred, tz=UTC) if isinstance(occurred, int) else datetime.now(UTC)
    )
    return InboundMessage(
        channel=Channel.TELEGRAM,
        external_contact_id=str(sender["id"]),
        external_message_id=f"tg:{message.get('message_id')}",
        text=text,
        occurred_at=occurred_at,
        contact_name=name,
    )


def _meta(payload: dict[str, Any], channel: Channel) -> InboundMessage | None:
    try:
        value = payload["entry"][0]["changes"][0]["value"]
        msg = value["messages"][0]
    except (KeyError, IndexError, TypeError):
        return None
    text = (msg.get("text") or {}).get("body")
    contact_id = msg.get("from")
    if not isinstance(text, str) or not text or not contact_id:
        return None
    contacts = value.get("contacts") or [{}]
    name = (contacts[0].get("profile") or {}).get("name")
    return InboundMessage(
        channel=channel,
        external_contact_id=str(contact_id),
        external_message_id=f"{channel.value}:{msg.get('id')}",
        text=text,
        occurred_at=datetime.now(UTC),
        contact_name=name,
    )


def parse_inbound(provider: Provider, payload: dict[str, Any]) -> InboundMessage | None:
    if provider is Provider.TELEGRAM:
        return _telegram(payload)
    if provider is Provider.WHATSAPP:
        return _meta(payload, Channel.WHATSAPP)
    if provider is Provider.INSTAGRAM:
        return _meta(payload, Channel.INSTAGRAM)
    return None
