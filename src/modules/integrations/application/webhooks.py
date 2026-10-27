"""Webhook ingestion use case: verify -> parse -> deduplicate (inbox) -> schedule.

Processing happens asynchronously in the ``integrations`` queue; the HTTP
handler only acknowledges quickly so providers do not time out and retry.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from modules.integrations.application.ports import ProviderSpec, WebhookProcessingScheduler
from modules.integrations.domain.providers import (
    IntegrationNotConfiguredError,
    InvalidSignatureError,
)
from platform_core.application.ports import InboxStore
from shared.application.ports import UnitOfWork
from shared.domain.errors import DomainValidationError


@dataclass(frozen=True, slots=True)
class IngestResult:
    duplicate: bool
    event_id: str


class IngestWebhook:
    def __init__(
        self,
        inbox: InboxStore,
        uow: UnitOfWork,
        scheduler: WebhookProcessingScheduler,
    ) -> None:
        self._inbox = inbox
        self._uow = uow
        self._scheduler = scheduler

    def execute(
        self,
        *,
        spec: ProviderSpec,
        tenant_slug: str,
        headers: Mapping[str, str],
        body: bytes,
        correlation_id: str | None,
    ) -> IngestResult:
        verifier = spec.verifier()
        if verifier is None:
            raise IntegrationNotConfiguredError("Integration is not configured.")
        if not verifier.verify(headers, body):
            raise InvalidSignatureError("Invalid webhook signature.")
        try:
            payload: Any = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise DomainValidationError("Webhook body must be JSON.") from None
        if not isinstance(payload, dict):
            raise DomainValidationError("Webhook body must be a JSON object.")

        event_id = spec.event_id(payload, body)
        with self._uow:
            inbox_id = self._inbox.register(
                source=f"webhook.{spec.provider.value}",
                message_id=event_id,
                consumer="ingest",
                payload=payload,
                correlation_id=correlation_id,
            )
            if inbox_id is None:
                return IngestResult(duplicate=True, event_id=event_id)
            self._scheduler.schedule(
                tenant_slug=tenant_slug, provider=spec.provider, inbox_id=inbox_id
            )
        return IngestResult(duplicate=False, event_id=event_id)
