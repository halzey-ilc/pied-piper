from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol
from uuid import UUID

from modules.integrations.application.contracts import DeliveryResult, OutboundMessage
from modules.integrations.domain.providers import Provider


class WebhookVerifier(Protocol):
    def verify(self, headers: Mapping[str, str], body: bytes) -> bool: ...


class ProviderSpec(Protocol):
    """Provider-specific knowledge needed to ingest its webhooks."""

    @property
    def provider(self) -> Provider: ...

    def verifier(self) -> WebhookVerifier | None:
        """None when the integration has no secret configured."""
        ...

    def event_id(self, payload: Mapping[str, Any], body: bytes) -> str:
        """Stable id used for deduplication of provider retries."""
        ...


class ChannelAdapter(Protocol):
    """Outbound messaging port implemented per provider."""

    provider: Provider

    def send(self, message: OutboundMessage) -> DeliveryResult: ...


class WebhookProcessingScheduler(Protocol):
    def schedule(self, *, tenant_slug: str, provider: Provider, inbox_id: UUID) -> None:
        """Queue asynchronous processing after the current transaction commits."""
        ...
