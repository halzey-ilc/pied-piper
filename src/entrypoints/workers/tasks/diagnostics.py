"""Diagnostic tasks used by health checks and integration tests.

``diagnostics.fail`` only exists when DIAGNOSTIC_TASKS_ENABLED (never in production).
"""

from __future__ import annotations

from typing import Any

from celery import shared_task
from django.conf import settings

from platform_core.application.context import current_correlation_id


@shared_task(name="diagnostics.ping", ignore_result=False, acks_late=True)
def ping() -> dict[str, Any]:
    return {"pong": True, "correlation_id": current_correlation_id()}


if settings.DIAGNOSTIC_TASKS_ENABLED:

    @shared_task(name="diagnostics.fail", max_retries=0, acks_late=True)
    def fail() -> None:
        raise RuntimeError("diagnostic failure (expected)")
