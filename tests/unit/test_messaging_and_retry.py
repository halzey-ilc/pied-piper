"""Outbox relay, idempotent consumer and retry policy with in-memory fakes."""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import pytest

from platform_core.application.messaging import (
    EventHandlerRegistry,
    IdempotentConsumer,
    OutboxRelay,
)
from platform_core.application.ports import EventEnvelope, OutboxRecord
from shared.application.retry import RetryPolicy

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class FakeUow:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self) -> FakeUow:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.commits += 1
        else:
            self.rollbacks += 1


class FakeOutboxStore:
    def __init__(self, records: list[OutboxRecord]) -> None:
        self.records = records
        self.published: list[UUID] = []
        self.retries: dict[UUID, datetime] = {}
        self.failed: dict[UUID, str] = {}

    def claim_batch(self, limit: int, now: datetime) -> Sequence[OutboxRecord]:
        return self.records[:limit]

    def mark_published(self, event_id: UUID, now: datetime) -> None:
        self.published.append(event_id)

    def schedule_retry(self, event_id: UUID, error: str, available_at: datetime) -> None:
        self.retries[event_id] = available_at

    def mark_failed(self, event_id: UUID, error: str) -> None:
        self.failed[event_id] = error


class FlakyPublisher:
    def __init__(self, failing: set[UUID]) -> None:
        self.failing = failing
        self.sent: list[UUID] = []

    def publish(self, envelope: EventEnvelope) -> None:
        if envelope.event_id in self.failing:
            raise ConnectionError("amqp://crm:hunter2@rabbitmq:5672 unreachable")
        self.sent.append(envelope.event_id)


def _record(attempts: int = 0) -> OutboxRecord:
    return OutboxRecord(
        envelope=EventEnvelope(
            event_id=uuid4(),
            event_type="t",
            aggregate_type="a",
            aggregate_id="1",
            occurred_at=NOW,
            payload={},
            correlation_id=None,
            tenant_slug="acme",
        ),
        attempts=attempts,
    )


def test_relay_publishes_retries_and_dead_letters() -> None:
    ok, flaky, exhausted = _record(), _record(attempts=0), _record(attempts=4)
    store = FakeOutboxStore([ok, flaky, exhausted])
    publisher = FlakyPublisher({flaky.envelope.event_id, exhausted.envelope.event_id})
    policy = RetryPolicy(
        max_attempts=5, base_delay_seconds=1, max_delay_seconds=60, rng=random.Random(1)
    )

    stats = OutboxRelay(store, publisher, FakeUow(), policy, clock=lambda: NOW).relay_once()

    assert (stats.published, stats.retried, stats.failed) == (1, 1, 1)
    assert store.published == [ok.envelope.event_id]
    assert NOW <= store.retries[flaky.envelope.event_id] <= NOW + timedelta(seconds=1)
    error = store.failed[exhausted.envelope.event_id]
    assert "hunter2" not in error and "ConnectionError" in error


def test_idempotent_consumer_runs_handler_once() -> None:
    class Inbox:
        def __init__(self) -> None:
            self.seen: set[tuple[str, str, str]] = set()
            self.processed: list[UUID] = []

        def register(
            self,
            *,
            source: str,
            message_id: str,
            consumer: str,
            payload: dict[str, Any],
            correlation_id: str | None,
        ) -> UUID | None:
            key = (source, message_id, consumer)
            if key in self.seen:
                return None
            self.seen.add(key)
            return uuid4()

        def mark_processed(self, inbox_id: UUID) -> None:
            self.processed.append(inbox_id)

    calls: list[dict[str, Any]] = []
    consumer = IdempotentConsumer(Inbox(), FakeUow())
    kwargs: dict[str, Any] = {
        "source": "event",
        "message_id": "e1",
        "consumer": "h",
        "payload": {"x": 1},
        "correlation_id": None,
        "handler": calls.append,
    }
    assert consumer.consume(**kwargs).duplicate is False
    assert consumer.consume(**kwargs).duplicate is True
    assert calls == [{"x": 1}]


def test_handler_failure_rolls_back_unit_of_work() -> None:
    uow = FakeUow()

    class Inbox:
        def register(self, **_: Any) -> UUID | None:
            return uuid4()

        def mark_processed(self, inbox_id: UUID) -> None:  # pragma: no cover
            raise AssertionError("must not be reached")

    def boom(_: dict[str, Any]) -> None:
        raise ValueError("handler failed")

    with pytest.raises(ValueError, match="handler failed"):
        IdempotentConsumer(Inbox(), uow).consume(
            source="s", message_id="m", consumer="c", payload={}, correlation_id=None, handler=boom
        )
    assert uow.rollbacks == 1 and uow.commits == 0


def test_handler_registry_rejects_duplicates() -> None:
    registry = EventHandlerRegistry()
    registry.register("customers.customer_created", "crm.notify", lambda p: None)
    with pytest.raises(ValueError, match="already registered"):
        registry.register("customers.customer_created", "crm.notify", lambda p: None)


def test_retry_policy_exponential_backoff_with_jitter() -> None:
    policy = RetryPolicy(
        max_attempts=4, base_delay_seconds=2, max_delay_seconds=10, rng=random.Random(42)
    )
    assert [policy.backoff_ceiling(a) for a in (1, 2, 3, 4, 5)] == [2, 4, 8, 10, 10]
    delays = [policy.delay_for(3) for _ in range(200)]
    assert all(0 <= d <= 8 for d in delays)
    assert len({round(d, 3) for d in delays}) > 100  # jittered, not constant
    assert policy.should_retry(3) and not policy.should_retry(4)
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)
