"""Prometheus metrics. Labels are bounded (route templates, task names) to avoid
cardinality explosions; tenant slugs and ids are never used as labels."""

from __future__ import annotations

import os

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

HTTP_REQUESTS = Counter("crm_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "crm_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
TASKS = Counter("crm_celery_tasks_total", "Celery task outcomes", ["task", "state"])
TASK_DURATION = Histogram(
    "crm_celery_task_duration_seconds",
    "Celery task run time",
    ["task"],
    buckets=(0.05, 0.1, 0.5, 1, 5, 15, 60, 300),
)
OUTBOX_EVENTS = Counter("crm_outbox_events_total", "Outbox relay results", ["result"])
WEBHOOKS = Counter("crm_webhooks_total", "Inbound webhooks", ["provider", "result"])


def render_latest() -> tuple[bytes, str]:
    """Render metrics; aggregates all worker processes when multiprocess mode is on."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        from prometheus_client import multiprocess

        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
        return generate_latest(registry), CONTENT_TYPE_LATEST
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
