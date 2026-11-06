"""Outbound channel adapters (extension points).

Request shapes follow the providers' public HTTP APIs but are NOT verified
against live accounts. Nothing in the scaffold instantiates these with real
credentials; tests use httpx.MockTransport.
"""

from __future__ import annotations

import httpx

from modules.integrations.application.contracts import DeliveryResult, OutboundMessage
from modules.integrations.domain.providers import Provider
from modules.integrations.infrastructure.http import send_with_retry
from shared.application.retry import RetryPolicy


class WhatsAppCloudAdapter:
    provider = Provider.WHATSAPP

    def __init__(
        self, client: httpx.Client, phone_number_id: str, access_token: str, policy: RetryPolicy
    ) -> None:
        self._client = client
        self._phone_number_id = phone_number_id
        self._token = access_token
        self._policy = policy

    def send(self, message: OutboundMessage) -> DeliveryResult:
        response = send_with_retry(
            lambda: self._client.post(
                f"/{self._phone_number_id}/messages",
                headers={"Authorization": f"Bearer {self._token}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": message.recipient_external_id,
                    "type": "text",
                    "text": {"body": message.text},
                },
            ),
            self._policy,
        )
        data = response.json()
        message_ids = data.get("messages") or [{}]
        return DeliveryResult(
            provider=self.provider,
            external_message_id=message_ids[0].get("id"),
            accepted=True,
        )


class GreenApiAdapter:
    """The token is part of the URL, so requests must never be logged at INFO."""

    provider = Provider.GREEN_API

    def __init__(
        self, client: httpx.Client, instance_id: str, token: str, policy: RetryPolicy
    ) -> None:
        self._client = client
        self._instance_id = instance_id
        self._token = token
        self._policy = policy

    def send(self, message: OutboundMessage) -> DeliveryResult:
        recipient = message.recipient_external_id
        response = send_with_retry(
            lambda: self._client.post(
                f"/waInstance{self._instance_id}/sendMessage/{self._token}",
                json={
                    "chatId": recipient if "@" in recipient else f"{recipient}@c.us",
                    "message": message.text,
                },
            ),
            self._policy,
        )
        return DeliveryResult(
            provider=self.provider,
            external_message_id=response.json().get("idMessage"),
            accepted=True,
        )


class TelegramBotAdapter:
    provider = Provider.TELEGRAM

    def __init__(self, client: httpx.Client, bot_token: str, policy: RetryPolicy) -> None:
        self._client = client
        self._token = bot_token
        self._policy = policy

    def send(self, message: OutboundMessage) -> DeliveryResult:
        response = send_with_retry(
            lambda: self._client.post(
                f"/bot{self._token}/sendMessage",
                json={"chat_id": message.recipient_external_id, "text": message.text},
            ),
            self._policy,
        )
        result = response.json().get("result") or {}
        message_id = result.get("message_id")
        return DeliveryResult(
            provider=self.provider,
            external_message_id=str(message_id) if message_id is not None else None,
            accepted=True,
        )
