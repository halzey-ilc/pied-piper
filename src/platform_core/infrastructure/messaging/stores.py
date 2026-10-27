"""Django adapters for the outbox/inbox ports. Every adapter is bound to one database alias."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from platform_core.application.context import current_correlation_id, current_tenant
from platform_core.application.ports import EventEnvelope, OutboxRecord
from platform_core.infrastructure.messaging.models import (
    InboxMessage,
    InboxStatus,
    OutboxMessage,
    OutboxStatus,
)
from shared.domain.events import DomainEvent


class DjangoEventOutbox:
    def __init__(self, using: str) -> None:
        self.using = using

    def add(self, event: DomainEvent, *, aggregate_type: str, aggregate_id: str) -> None:
        OutboxMessage.objects.using(self.using).create(
            id=event.event_id,
            event_type=event.event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=event.payload(),
            correlation_id=current_correlation_id() or "",
            occurred_at=event.occurred_at,
        )


class DjangoOutboxStore:
    def __init__(self, using: str, tenant_slug: str | None) -> None:
        self.using = using
        self.tenant_slug = tenant_slug

    def claim_batch(self, limit: int, now: datetime) -> Sequence[OutboxRecord]:
        rows = (
            OutboxMessage.objects.using(self.using)
            .select_for_update(skip_locked=True)
            .filter(status=OutboxStatus.PENDING, available_at__lte=now)
            .order_by("available_at", "occurred_at")[:limit]
        )
        return [
            OutboxRecord(
                envelope=EventEnvelope(
                    event_id=row.id,
                    event_type=row.event_type,
                    aggregate_type=row.aggregate_type,
                    aggregate_id=row.aggregate_id,
                    occurred_at=row.occurred_at,
                    payload=row.payload,
                    correlation_id=row.correlation_id or None,
                    tenant_slug=self.tenant_slug,
                ),
                attempts=row.attempts,
            )
            for row in rows
        ]

    def mark_published(self, event_id: UUID, now: datetime) -> None:
        OutboxMessage.objects.using(self.using).filter(id=event_id).update(
            status=OutboxStatus.PUBLISHED, published_at=now, attempts=F("attempts") + 1
        )

    def schedule_retry(self, event_id: UUID, error: str, available_at: datetime) -> None:
        OutboxMessage.objects.using(self.using).filter(id=event_id).update(
            attempts=F("attempts") + 1, available_at=available_at, last_error=error
        )

    def mark_failed(self, event_id: UUID, error: str) -> None:
        OutboxMessage.objects.using(self.using).filter(id=event_id).update(
            status=OutboxStatus.FAILED, attempts=F("attempts") + 1, last_error=error
        )


class DjangoInboxStore:
    def __init__(self, using: str) -> None:
        self.using = using

    def register(
        self,
        *,
        source: str,
        message_id: str,
        consumer: str,
        payload: dict[str, Any],
        correlation_id: str | None,
    ) -> UUID | None:
        try:
            # Savepoint: a duplicate must not poison the caller's transaction.
            with transaction.atomic(using=self.using):
                row = InboxMessage.objects.using(self.using).create(
                    source=source,
                    message_id=message_id,
                    consumer=consumer,
                    payload=payload,
                    correlation_id=correlation_id or "",
                )
        except IntegrityError:
            return None
        return row.id

    def mark_processed(self, inbox_id: UUID) -> None:
        InboxMessage.objects.using(self.using).filter(id=inbox_id).update(
            status=InboxStatus.PROCESSED, processed_at=timezone.now(), attempts=F("attempts") + 1
        )

    def get_payload(self, inbox_id: UUID) -> dict[str, Any] | None:
        row = InboxMessage.objects.using(self.using).filter(id=inbox_id).first()
        return None if row is None else dict(row.payload)


def outbox_for_current_database() -> DjangoEventOutbox:
    tenant = current_tenant()
    return DjangoEventOutbox(tenant.db_alias if tenant else "default")
