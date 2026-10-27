"""Celery task base class with the platform retry policy."""

from __future__ import annotations

from celery import Task

from shared.application.retry import TransientError


# celery.Task is generic to type-checkers (celery-types) but is NOT subscriptable
# at runtime, so it must be subclassed bare.
class ResilientTask(Task):  # type: ignore[type-arg]
    """Retries TransientError with exponential backoff + jitter, then dead-letters.

    Tasks built on this class must be idempotent: acks_late means a task can be
    delivered again after a worker crash.
    """

    autoretry_for = (TransientError,)
    retry_backoff = 2
    retry_backoff_max = 600
    retry_jitter = True
    max_retries = 5
    acks_late = True
