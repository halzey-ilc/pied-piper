"""Transactional outbox relay and idempotent inbox consumption.

Delivery guarantee provided here: at-least-once from the outbox to the broker
(a crash between publish and mark_published re-publishes the event).
Consumers therefore deduplicate through the inbox.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from platform_core.application.ports import InboxStore, MessagePublisher, OutboxStore
from shared.application.ports import UnitOfWork
from shared.application.retry import RetryPolicy

logger = logging.getLogger(__name__)

_MAX_ERROR_LENGTH = 300


_URL_CREDENTIALS_RE = re.compile(r"://[^/@\s]+@")


def _describe(exc: BaseException) -> str:
    """Error summary for the database: type + truncated message, URL credentials removed."""
    message = _URL_CREDENTIALS_RE.sub("://***@", str(exc))
    return f"{type(exc).__name__}: {message}"[:_MAX_ERROR_LENGTH]


@dataclass(frozen=True, slots=True)
class RelayStats:
    published: int = 0
    retried: int = 0
    failed: int = 0


class OutboxRelay:
    def __init__(
        self,
        store: OutboxStore,
        publisher: MessagePublisher,
        uow: UnitOfWork,
        policy: RetryPolicy,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._publisher = publisher
        self._uow = uow
        self._policy = policy
        self._clock = clock

    def relay_once(self, limit: int = 100) -> RelayStats:
        published = retried = failed = 0
        with self._uow:
            for record in self._store.claim_batch(limit, self._clock()):
                event_id = record.envelope.event_id
                try:
                    self._publisher.publish(record.envelope)
                except Exception as exc:  # noqa: BLE001 - every publish failure is recorded
                    attempt = record.attempts + 1
                    if self._policy.should_retry(attempt):
                        delay = self._policy.delay_for(attempt)
                        self._store.schedule_retry(
                            event_id, _describe(exc), self._clock() + timedelta(seconds=delay)
                        )
                        retried += 1
                    else:
                        self._store.mark_failed(event_id, _describe(exc))
                        failed += 1
                        logger.error(
                            "outbox_message_failed",
                            extra={"event_id": str(event_id), "attempts": attempt},
                        )
                    continue
                self._store.mark_published(event_id, self._clock())
                published += 1
        return RelayStats(published=published, retried=retried, failed=failed)


@dataclass(frozen=True, slots=True)
class ConsumeResult:
    duplicate: bool


class IdempotentConsumer:
    """Runs a handler at most once per (source, message_id, consumer) within one database.

    The inbox row and the handler's own writes share one transaction, so a failing
    handler rolls back the inbox row too and the message can be retried.
    """

    def __init__(self, inbox: InboxStore, uow: UnitOfWork) -> None:
        self._inbox = inbox
        self._uow = uow

    def consume(
        self,
        *,
        source: str,
        message_id: str,
        consumer: str,
        payload: dict[str, Any],
        correlation_id: str | None,
        handler: Callable[[dict[str, Any]], None],
    ) -> ConsumeResult:
        with self._uow:
            inbox_id = self._inbox.register(
                source=source,
                message_id=message_id,
                consumer=consumer,
                payload=payload,
                correlation_id=correlation_id,
            )
            if inbox_id is None:
                return ConsumeResult(duplicate=True)
            handler(payload)
            self._inbox.mark_processed(inbox_id)
        return ConsumeResult(duplicate=False)


EventHandler = Callable[[dict[str, Any]], None]


class EventHandlerRegistry:
    """Maps event types to named handlers. Modules register handlers at start-up."""

    def __init__(self) -> None:
        self._handlers: dict[str, dict[str, EventHandler]] = {}

    def register(self, event_type: str, consumer: str, handler: EventHandler) -> None:
        handlers = self._handlers.setdefault(event_type, {})
        if consumer in handlers:
            raise ValueError(f"Handler {consumer!r} already registered for {event_type!r}")
        handlers[consumer] = handler

    def handlers_for(self, event_type: str) -> dict[str, EventHandler]:
        return dict(self._handlers.get(event_type, {}))


event_handlers = EventHandlerRegistry()
