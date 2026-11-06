"""Whether the WhatsApp number is connected, and the QR code that connects it.

The token sits in Green API's URLs, so nothing here logs a URL or a response.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from modules.integrations.infrastructure.http import build_http_client

CONNECTED_STATE = "authorized"
UNAVAILABLE_STATE = "unavailable"
NOT_CONFIGURED_STATE = "not_configured"


@dataclass(frozen=True, slots=True)
class ConnectionStatus:
    provider: str
    configured: bool
    connected: bool
    state: str


@dataclass(frozen=True, slots=True)
class QrCode:
    status: str  # "qr", "connected" or "unavailable"
    image: str | None = None  # data URL, ready for <img src>


class GreenApiConnection:
    def __init__(
        self,
        *,
        base_url: str,
        instance_id: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._instance_id = instance_id
        self._token = token
        self._transport = transport

    def _request(
        self, method: str, name: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        path = f"/waInstance{self._instance_id}/{name}/{self._token}"
        try:
            with build_http_client(self._base_url, transport=self._transport) as client:
                response = client.request(method, path, json=body)
            data = response.json() if response.is_success else None
        except (httpx.HTTPError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def status(self) -> ConnectionStatus:
        data = self._request("GET", "getStateInstance")
        state = str(data.get("stateInstance")) if data else UNAVAILABLE_STATE
        return ConnectionStatus(
            provider="green_api",
            configured=True,
            connected=state == CONNECTED_STATE,
            state=state,
        )

    def qr(self) -> QrCode:
        data = self._request("GET", "qr")
        kind = data.get("type") if data else None
        if kind == "qrCode" and data:
            return QrCode(status="qr", image=f"data:image/png;base64,{data.get('message')}")
        if kind == "alreadyLogged":
            return QrCode(status="connected")
        return QrCode(status="unavailable")

    def set_webhook(self, *, url: str, token: str) -> bool:
        data = self._request(
            "POST",
            "setSettings",
            {
                "webhookUrl": url,
                "webhookUrlToken": f"Bearer {token}",
                "incomingWebhook": "yes",
                "outgoingMessageWebhook": "yes",
                "outgoingAPIMessageWebhook": "no",
                "outgoingWebhook": "yes",
            },
        )
        return bool(data and data.get("saveSettings"))


class CloudApiConnection:
    """Meta has no QR code and no session to lose: configured means connected."""

    def __init__(self, *, configured: bool) -> None:
        self._configured = configured

    def status(self) -> ConnectionStatus:
        return ConnectionStatus(
            provider="cloud_api",
            configured=self._configured,
            connected=self._configured,
            state=CONNECTED_STATE if self._configured else NOT_CONFIGURED_STATE,
        )

    def qr(self) -> QrCode:
        return QrCode(status="connected" if self._configured else "unavailable")


class NoConnection:
    """Green API selected but not configured, or a company that does not own the number."""

    def __init__(self, provider: str) -> None:
        self._provider = provider

    def status(self) -> ConnectionStatus:
        return ConnectionStatus(
            provider=self._provider,
            configured=False,
            connected=False,
            state=NOT_CONFIGURED_STATE,
        )

    def qr(self) -> QrCode:
        return QrCode(status="unavailable")
