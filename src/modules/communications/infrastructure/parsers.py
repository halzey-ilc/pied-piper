"""Parse a provider webhook payload into normalized inbound messages.

Shapes follow the providers' public webhook formats but are NOT verified against
live accounts. A payload with no message (e.g. a status update) parses to nothing
and is simply ignored.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from modules.communications.application.service import DeliveryFailure, InboundMessage
from modules.communications.domain.conversation import Channel
from modules.integrations.domain.providers import Provider

# Media is not downloaded yet: the manager sees what was sent and opens it in the
# WhatsApp app. These strings are shown in the CRM, hence Russian.
_MEDIA_LABELS = {
    "image": "[Фото]",
    "video": "[Видео]",
    "audio": "[Голосовое сообщение]",
    "document": "[Документ]",
    "sticker": "[Стикер]",
    "location": "[Геолокация]",
    "contacts": "[Контакт]",
    "contact": "[Контакт]",
}
_UNSUPPORTED_LABEL = "[Сообщение не поддерживается]"
_IGNORED_TYPES = frozenset({"reaction"})
# outgoingAPIMessageReceived is left out: the CRM already stored what it sent.
_GREEN_MESSAGE_WEBHOOKS = frozenset({"incomingMessageReceived", "outgoingMessageReceived"})
_GREEN_FAILED_STATUSES = frozenset({"failed", "noAccount", "notInGroup", "suspended", "yellowCard"})


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


def _meta_values(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    entries = payload.get("entry")
    for entry in entries if isinstance(entries, list) else []:
        changes = entry.get("changes") if isinstance(entry, dict) else None
        for change in changes if isinstance(changes, list) else []:
            value = change.get("value") if isinstance(change, dict) else None
            if isinstance(value, dict):
                yield value


def _meta_text(message: dict[str, Any]) -> str | None:
    """The text to show for a message, or None if it should not appear in the chat."""
    kind = message.get("type") or "text"
    if kind in _IGNORED_TYPES:
        return None
    if kind == "text":
        body = (message.get("text") or {}).get("body")
        return body if isinstance(body, str) and body.strip() else None
    if kind == "button":
        return (message.get("button") or {}).get("text") or _UNSUPPORTED_LABEL
    if kind == "interactive":
        interactive = message.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        return reply.get("title") or _UNSUPPORTED_LABEL
    label = _MEDIA_LABELS.get(kind)
    if label is None:
        return _UNSUPPORTED_LABEL
    media = message.get(kind)
    caption = media.get("caption") if isinstance(media, dict) else None
    return f"{label} {caption}" if isinstance(caption, str) and caption.strip() else label


def _meta_time(message: dict[str, Any]) -> datetime:
    try:
        return datetime.fromtimestamp(int(message["timestamp"]), tz=UTC)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return datetime.now(UTC)


def _meta_names(value: dict[str, Any]) -> tuple[dict[str, str], str | None]:
    """Profile names by WhatsApp id, plus the first name for payloads without ids."""
    names: dict[str, str] = {}
    first: str | None = None
    for contact in value.get("contacts") or []:
        name = (contact.get("profile") or {}).get("name")
        if isinstance(name, str) and name:
            first = first or name
            if contact.get("wa_id"):
                names[str(contact["wa_id"])] = name
    return names, first


def _meta(payload: dict[str, Any], channel: Channel) -> list[InboundMessage]:
    parsed: list[InboundMessage] = []
    for value in _meta_values(payload):
        names, first_name = _meta_names(value)
        for message in value.get("messages") or []:
            text = _meta_text(message)
            contact_id = message.get("from")
            if text is None or not contact_id or not message.get("id"):
                continue
            parsed.append(
                InboundMessage(
                    channel=channel,
                    external_contact_id=str(contact_id),
                    external_message_id=f"{channel.value}:{message['id']}",
                    text=text,
                    occurred_at=_meta_time(message),
                    contact_name=names.get(str(contact_id), first_name),
                )
            )
        # Coexistence: replies typed in the WhatsApp Business app on the phone.
        for echo in value.get("message_echoes") or []:
            text = _meta_text(echo)
            contact_id = echo.get("to")
            if text is None or not contact_id or not echo.get("id"):
                continue
            parsed.append(
                InboundMessage(
                    channel=channel,
                    external_contact_id=str(contact_id),
                    external_message_id=f"{channel.value}:{echo['id']}",
                    text=text,
                    occurred_at=_meta_time(echo),
                    from_business=True,
                )
            )
    return parsed


def _green_text(data: dict[str, Any]) -> str | None:
    """The text to show for a message, or None if it should not appear in the chat."""
    kind = data.get("typeMessage")
    if kind == "reactionMessage":
        return None
    if kind == "textMessage":
        text = (data.get("textMessageData") or {}).get("textMessage")
    elif kind in ("extendedTextMessage", "quotedMessage"):
        text = (data.get("extendedTextMessageData") or {}).get("text")
    else:
        label = _MEDIA_LABELS.get(str(kind).removesuffix("Message"))
        if label is None:
            return _UNSUPPORTED_LABEL
        caption = (data.get("fileMessageData") or {}).get("caption")
        return f"{label} {caption}" if isinstance(caption, str) and caption.strip() else label
    return text if isinstance(text, str) and text.strip() else None


def _green_api(payload: dict[str, Any]) -> InboundMessage | None:
    kind = payload.get("typeWebhook")
    if kind not in _GREEN_MESSAGE_WEBHOOKS or not payload.get("idMessage"):
        return None
    sender = payload.get("senderData") or {}
    chat = sender.get("chatId")
    # Only people: a group is not a lead, and "@lid" is a person without a phone.
    if not isinstance(chat, str) or not chat.endswith(("@c.us", "@lid")):
        return None
    text = _green_text(payload.get("messageData") or {})
    if text is None:
        return None
    from_business = kind == "outgoingMessageReceived"
    return InboundMessage(
        channel=Channel.WHATSAPP,
        external_contact_id=chat.removesuffix("@c.us"),
        external_message_id=f"{Channel.WHATSAPP.value}:{payload['idMessage']}",
        text=text,
        occurred_at=_meta_time(payload),
        contact_name=None if from_business else sender.get("senderName") or sender.get("chatName"),
        from_business=from_business,
    )


def parse_messages(provider: Provider, payload: dict[str, Any]) -> list[InboundMessage]:
    """Every message in the payload; Meta may batch several into one webhook."""
    if provider is Provider.TELEGRAM:
        message = _telegram(payload)
        return [message] if message is not None else []
    if provider is Provider.WHATSAPP:
        return _meta(payload, Channel.WHATSAPP)
    if provider is Provider.GREEN_API:
        message = _green_api(payload)
        return [message] if message is not None else []
    if provider is Provider.INSTAGRAM:
        return _meta(payload, Channel.INSTAGRAM)
    return []


def parse_inbound(provider: Provider, payload: dict[str, Any]) -> InboundMessage | None:
    messages = parse_messages(provider, payload)
    return messages[0] if messages else None


def _green_failures(payload: dict[str, Any]) -> list[DeliveryFailure]:
    status = payload.get("status")
    message_id = payload.get("idMessage")
    if (
        payload.get("typeWebhook") != "outgoingMessageStatus"
        or status not in _GREEN_FAILED_STATUSES
        or not message_id
    ):
        return []
    return [
        DeliveryFailure(
            channel=Channel.WHATSAPP,
            external_message_id=f"{Channel.WHATSAPP.value}:{message_id}",
            code=str(status),
        )
    ]


def parse_delivery_failures(provider: Provider, payload: dict[str, Any]) -> list[DeliveryFailure]:
    """WhatsApp accepts a send and reports failure later (e.g. 131047: the 24-hour
    window has closed), so a failed status is the only sign it never arrived."""
    if provider is Provider.GREEN_API:
        return _green_failures(payload)
    if provider is not Provider.WHATSAPP:
        return []
    failures: list[DeliveryFailure] = []
    for value in _meta_values(payload):
        for item in value.get("statuses") or []:
            if item.get("status") != "failed" or not item.get("id"):
                continue
            errors = item.get("errors") or [{}]
            code = errors[0].get("code")
            failures.append(
                DeliveryFailure(
                    channel=Channel.WHATSAPP,
                    external_message_id=f"{Channel.WHATSAPP.value}:{item['id']}",
                    code=str(code) if code is not None else None,
                )
            )
    return failures
