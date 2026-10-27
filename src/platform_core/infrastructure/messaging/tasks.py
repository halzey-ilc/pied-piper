"""Celery task base class with the platform retry policy."""

from __future__ import annotations

from typing import Any

from celery import Task

from shared.application.retry import TransientError


class ResilientTask(Task[..., Any]):
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
