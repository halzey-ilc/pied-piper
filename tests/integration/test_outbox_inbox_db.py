"""Outbox / inbox against real PostgreSQL: atomicity, constraints, relay state transitions."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction

from modules.customers.domain.customer import Customer
from platform_core.application.messaging import OutboxRelay
from platform_core.application.ports import EventEnvelope
from platform_core.infrastructure.messaging.models import InboxMessage, OutboxMessage, OutboxStatus
from platform_core.infrastructure.messaging.stores import (
    DjangoEventOutbox,
    DjangoInboxStore,
    DjangoOutboxStore,
)
from shared.application.retry import RetryPolicy
from shared.infrastructure.unit_of_work import DjangoUnitOfWork
from tests.conftest import ALL_DATABASES

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)
ALIAS = "tenant_acme"


def _event() -> object:
    _, event = Customer.register(name="X", owner_user_id=uuid4())
    return event


def test_outbox_row_rolls_back_with_business_transaction() -> None:
    outbox = DjangoEventOutbox(ALIAS)

    class Boom(Exception):
        pass

    with pytest.raises(Boom), DjangoUnitOfWork(ALIAS):
        outbox.add(_event(), aggregate_type="customer", aggregate_id="1")  # type: ignore[arg-type]
        raise Boom
    assert OutboxMessage.objects.using(ALIAS).count() == 0

    with DjangoUnitOfWork(ALIAS):
        outbox.add(_event(), aggregate_type="customer", aggregate_id="1")  # type: ignore[arg-type]
    assert OutboxMessage.objects.using(ALIAS).count() == 1


def test_inbox_unique_constraint_is_enforced_by_postgres() -> None:
    store = DjangoInboxStore(ALIAS)
    kwargs: dict[str, Any] = {
        "source": "webhook.telegram",
        "message_id": "update:1",
        "consumer": "ingest",
        "payload": {},
        "correlation_id": None,
    }
    assert store.register(**kwargs) is not None
    assert store.register(**kwargs) is None
    with pytest.raises(IntegrityError), transaction.atomic(using=ALIAS):
        InboxMessage.objects.using(ALIAS).create(
            source="webhook.telegram", message_id="update:1", consumer="ingest", payload={}
        )


class _Publisher:
    def __init__(self, fail: bool) -> None:
        self.fail = fail
        self.sent: list[EventEnvelope] = []

    def publish(self, envelope: EventEnvelope) -> None:
        if self.fail:
            raise ConnectionError("broker down")
        self.sent.append(envelope)


def test_relay_state_transitions_in_postgres() -> None:
    with DjangoUnitOfWork(ALIAS):
        DjangoEventOutbox(ALIAS).add(_event(), aggregate_type="customer", aggregate_id="1")  # type: ignore[arg-type]
    store = DjangoOutboxStore(ALIAS, "acme")
    now = datetime.now(UTC)
    policy = RetryPolicy(
        max_attempts=2, base_delay_seconds=60, max_delay_seconds=60, rng=random.Random(3)
    )

    stats = OutboxRelay(
        store, _Publisher(fail=True), DjangoUnitOfWork(ALIAS), policy, clock=lambda: now
    ).relay_once()
    row = OutboxMessage.objects.using(ALIAS).get()
    assert stats.retried == 1 and row.status == OutboxStatus.PENDING and row.attempts == 1
    assert row.available_at > now - timedelta(seconds=1) and "broker down" in row.last_error

    # Not due yet -> not claimed.
    assert (
        OutboxRelay(store, _Publisher(False), DjangoUnitOfWork(ALIAS), policy, clock=lambda: now)
        .relay_once()
        .published
        == 0
    )

    later = now + timedelta(minutes=5)
    failing = OutboxRelay(
        store, _Publisher(fail=True), DjangoUnitOfWork(ALIAS), policy, clock=lambda: later
    )
    assert failing.relay_once().failed == 1
    assert OutboxMessage.objects.using(ALIAS).get().status == OutboxStatus.FAILED

    OutboxMessage.objects.using(ALIAS).update(status=OutboxStatus.PENDING, attempts=0)
    publisher = _Publisher(fail=False)
    ok = OutboxRelay(
        store, publisher, DjangoUnitOfWork(ALIAS), policy, clock=lambda: later
    ).relay_once()
    row = OutboxMessage.objects.using(ALIAS).get()
    assert (
        ok.published == 1 and row.status == OutboxStatus.PUBLISHED and row.published_at is not None
    )
    assert publisher.sent[0].tenant_slug == "acme"
