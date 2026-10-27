"""Real backing services (acceptance items 3-5).

These tests need the docker compose stack (PostgreSQL, Redis, RabbitMQ and a
running Celery worker). If a service is missing they FAIL; they are never
skipped silently.
"""

from __future__ import annotations

import time
import uuid

import pytest
import redis
from django.conf import settings
from django.db import connections
from kombu import Connection, Queue

from config.celery import app as celery_app
from platform_core.application.context import correlation_scope
from platform_core.infrastructure.messaging.queues import dead_letter_queue_name

pytestmark = pytest.mark.integration


@pytest.mark.django_db(databases=["default", "tenant_acme", "tenant_globex"])
@pytest.mark.parametrize("alias", ["default", "tenant_acme", "tenant_globex"])
def test_postgresql_is_reachable(alias: str) -> None:
    with connections[alias].cursor() as cursor:
        cursor.execute("SELECT current_database(), version()")
        database, version = cursor.fetchone()
    assert database.startswith("test_")
    assert version.startswith("PostgreSQL")


def test_redis_is_reachable() -> None:
    client = redis.Redis.from_url(settings.REDIS_CACHE_URL, socket_connect_timeout=3)
    key = f"it:{uuid.uuid4().hex}"
    assert client.ping() is True
    client.set(key, "1", ex=10)
    assert client.get(key) == b"1"
    client.delete(key)


def test_rabbitmq_is_reachable() -> None:
    with Connection(settings.CELERY_BROKER_URL, connect_timeout=5) as conn:
        conn.ensure_connection(max_retries=1)
        assert conn.connected


def test_celery_worker_executes_task_and_propagates_correlation_id() -> None:
    with correlation_scope(correlation_id="it-corr-0001"):
        result = celery_app.send_task("diagnostics.ping")
    payload = result.get(timeout=30)
    assert payload == {"pong": True, "correlation_id": "it-corr-0001"}


def test_finally_failed_task_is_dead_lettered() -> None:
    dlq = dead_letter_queue_name("diagnostics")
    task_id = uuid.uuid4().hex
    with Connection(settings.CELERY_BROKER_URL, connect_timeout=5) as conn:
        channel = conn.channel()
        queue = Queue(dlq, durable=True).bind(channel)

        celery_app.send_task("diagnostics.fail", task_id=task_id)
        deadline = time.monotonic() + 30
        found = False
        while time.monotonic() < deadline and not found:
            message = queue.get(no_ack=False)
            if message is None:
                time.sleep(0.5)
                continue
            if message.headers.get("id") == task_id:
                message.ack()  # remove only our own diagnostic message
                found = True
            else:
                message.requeue()
                time.sleep(0.2)
        channel.close()  # type: ignore[attr-defined]
    assert found, f"task {task_id} did not reach {dlq}"
