"""Outbound channel senders.

WhatsAppCloudSender delivers through the Cloud API. A channel with no real account
gets UnconfiguredSender, which refuses to send, or outside production StubSender,
which returns a synthetic id so a message it "sends" never reaches anyone.
"""

from __future__ import annotations

from uuid import uuid4

import httpx

from modules.communications.application.ports import ChannelSender
from modules.communications.domain.conversation import Channel
from modules.integrations.application.contracts import OutboundMessage
from modules.integrations.domain.providers import Provider
from modules.integrations.infrastructure.adapters import WhatsAppCloudAdapter
from modules.integrations.infrastructure.http import build_http_client
from shared.application.retry import PermanentError, RetryPolicy, TransientError
from shared.domain.errors import ConflictError

# One quick retry: this runs inside the user's request and an open transaction.
_SEND_POLICY = RetryPolicy(max_attempts=2, base_delay_seconds=0.2, max_delay_seconds=0.5)


class ChannelDeliveryError(ConflictError):
    code = "channel_delivery_failed"


class ChannelNotConfiguredError(ConflictError):
    code = "channel_not_configured"


class StubSender:
    def send(self, *, channel: Channel, to_contact: str, text: str) -> str:
        return f"stub-{channel.value}-{uuid4().hex[:12]}"


class UnconfiguredSender:
    def send(self, *, channel: Channel, to_contact: str, text: str) -> str:
        raise ChannelNotConfiguredError(f"The {channel.value} channel is not connected.")


class WhatsAppCloudSender:
    """Sends WhatsApp messages; any other channel falls through to ``fallback``."""

    def __init__(
        self,
        *,
        base_url: str,
        phone_number_id: str,
        access_token: str,
        fallback: ChannelSender,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._phone_number_id = phone_number_id
        self._token = access_token
        self._fallback = fallback
        self._transport = transport

    def send(self, *, channel: Channel, to_contact: str, text: str) -> str:
        if channel is not Channel.WHATSAPP:
            return self._fallback.send(channel=channel, to_contact=to_contact, text=text)
        with build_http_client(self._base_url, transport=self._transport) as client:
            adapter = WhatsAppCloudAdapter(client, self._phone_number_id, self._token, _SEND_POLICY)
            try:
                result = adapter.send(
                    OutboundMessage(
                        provider=Provider.WHATSAPP,
                        recipient_external_id=to_contact,
                        text=text,
                        idempotency_key=uuid4().hex,
                    )
                )
            except (TransientError, PermanentError) as exc:
                raise ChannelDeliveryError("WhatsApp did not accept the message.") from exc
        if not result.external_message_id:
            raise ChannelDeliveryError("WhatsApp did not return a message id.")
        # Same namespace as parsed webhooks, so status callbacks find this message.
        return f"{Channel.WHATSAPP.value}:{result.external_message_id}"
