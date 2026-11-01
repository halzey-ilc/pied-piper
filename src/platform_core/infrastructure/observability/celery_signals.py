"""Celery signal handlers: correlation id propagation, structured context, metrics,
dead-letter topology declaration and per-process observability start-up.

Imported by config.celery before Django is configured, so module-level imports
must not touch Django models or settings.
"""

from __future__ import annotations

import inspect
import logging
import os
import time
from typing import Any

from celery import Celery, signals

from platform_core.application.context import (
    correlation_scope,
    current_correlation_id,
    sanitize_id,
)

logger = logging.getLogger(__name__)
_TIMERS: dict[str, tuple[float, Any]] = {}

# A non-reserved message header. "correlation_id" is a built-in Celery request
# field (set to the task id), so a custom header of that name would be masked.
CORRELATION_HEADER = "crm_correlation_id"


def _on_before_publish(headers: dict[str, Any] | None = None, **_: Any) -> None:
    if headers is not None and not headers.get(CORRELATION_HEADER):
        cid = current_correlation_id()
        if cid:
            headers[CORRELATION_HEADER] = cid


def _correlation_from(task: Any) -> str | None:
    request = getattr(task, "request", None)
    headers = getattr(request, "headers", None) or {}
    candidate = headers.get(CORRELATION_HEADER) or getattr(request, CORRELATION_HEADER, None)
    return sanitize_id(candidate) if isinstance(candidate, str) else None


def _on_prerun(task_id: str | None = None, task: Any = None, **_: Any) -> None:
    if not task_id:
        return
    scope = correlation_scope(
        request_id=sanitize_id(task_id.replace("-", "")), correlation_id=_correlation_from(task)
    )
    scope.__enter__()
    _TIMERS[task_id] = (time.perf_counter(), scope)


def _on_postrun(
    task_id: str | None = None, task: Any = None, state: str | None = None, **_: Any
) -> None:
    from platform_core.infrastructure.observability.metrics import TASK_DURATION, TASKS

    started = _TIMERS.pop(task_id, None) if task_id else None
    name = getattr(task, "name", "unknown")
    TASKS.labels(task=name, state=state or "UNKNOWN").inc()
    if started is not None:
        start, scope = started
        TASK_DURATION.labels(task=name).observe(time.perf_counter() - start)
        scope.__exit__(None, None, None)


def _on_retry(sender: Any = None, **_: Any) -> None:
    from platform_core.infrastructure.observability.metrics import TASKS

    TASKS.labels(task=getattr(sender, "name", "unknown"), state="RETRY").inc()


def _on_failure(
    sender: Any = None,
    exception: BaseException | None = None,
    args: tuple[Any, ...] | None = None,
    kwargs: dict[str, Any] | None = None,
    **_: Any,
) -> None:
    """Record a task that failed for good (retries are separate signals) for the platform
    administrator: task name, exception type, request id and — when the task takes one —
    the tenant slug. Never the exception message or the task arguments."""
    from platform_core.application.context import current_request_id
    from platform_core.domain.tenancy import validate_tenant_slug
    from platform_core.infrastructure.tenancy.services import error_recorder

    recorder = error_recorder()
    if recorder is None:
        return
    name = getattr(sender, "name", "unknown")
    tenant_slug: str | None = None
    try:
        bound = inspect.signature(sender.run).bind_partial(*(args or ()), **(kwargs or {}))
        candidate = bound.arguments.get("tenant_slug")
        if isinstance(candidate, str):
            tenant_slug = validate_tenant_slug(candidate)
    except Exception:  # noqa: BLE001 - an unexpected signature only means "no tenant"
        tenant_slug = None
    try:
        recorder.record(
            component=name.rsplit(".", 1)[0],
            view=name,
            method="TASK",
            route=name,
            exception_type=type(exception).__name__ if exception else "unknown",
            request_id=current_request_id(),
            tenant_slug=tenant_slug,
        )
    except Exception:
        logger.warning("task_error_record_failed", exc_info=True)


def _declare_dead_letter_queues(sender: Any = None, **_: Any) -> None:
    from platform_core.infrastructure.messaging.queues import build_dead_letter_queues

    app = sender.app
    with app.connection_for_write() as conn:
        channel = conn.default_channel
        for queue in build_dead_letter_queues():
            queue.bind(channel).declare()
    logger.info("dead_letter_queues_declared")


def _start_worker_metrics_server(**_: Any) -> None:
    port = os.environ.get("CELERY_METRICS_PORT")
    if not port:
        return
    from prometheus_client import CollectorRegistry, start_http_server

    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        from prometheus_client import multiprocess

        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
        start_http_server(int(port), registry=registry)
    else:
        start_http_server(int(port))


def _init_process_observability(**_: Any) -> None:
    from platform_core.infrastructure.observability.bootstrap import init_observability

    init_observability(component="worker")


def connect_celery_signals(app: Celery) -> None:
    signals.before_task_publish.connect(_on_before_publish, weak=False)
    signals.task_prerun.connect(_on_prerun, weak=False)
    signals.task_postrun.connect(_on_postrun, weak=False)
    signals.task_retry.connect(_on_retry, weak=False)
    signals.task_failure.connect(_on_failure, weak=False)
    signals.worker_ready.connect(_declare_dead_letter_queues, weak=False)
    signals.worker_init.connect(_start_worker_metrics_server, weak=False)
    signals.worker_process_init.connect(_init_process_observability, weak=False)
    signals.beat_init.connect(_init_process_observability, weak=False)
