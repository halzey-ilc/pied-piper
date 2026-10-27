"""Typed contracts exchanged with provider adapters (validated with Pydantic)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from modules.integrations.domain.providers import Provider


class InboundWebhook(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: Provider
    external_event_id: str = Field(min_length=1, max_length=255)
    received_at: datetime
    payload: dict[str, Any]


class OutboundMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: Provider
    recipient_external_id: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=4096)
    # Sent to providers that support it and used for our own deduplication.
    idempotency_key: str = Field(min_length=8, max_length=128)


class DeliveryResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: Provider
    external_message_id: str | None
    accepted: bool
