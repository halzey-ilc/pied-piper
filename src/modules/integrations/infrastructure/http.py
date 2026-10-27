"""Outbound HTTP policy for provider adapters: explicit timeouts, bounded retries
with exponential backoff + jitter, and error classification."""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from shared.application.retry import PermanentError, RetryPolicy, TransientError

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


def build_http_client(
    base_url: str,
    *,
    connect_timeout: float = 3.0,
    read_timeout: float = 10.0,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        follow_redirects=False,
        transport=transport,
    )


def classify(response: httpx.Response) -> None:
    """Raise TransientError/PermanentError for non-2xx responses (body not included)."""
    if response.is_success:
        return
    if response.status_code in RETRYABLE_STATUS:
        raise TransientError(f"Provider returned HTTP {response.status_code}")
    raise PermanentError(f"Provider returned HTTP {response.status_code}")


def send_with_retry(
    request: Callable[[], httpx.Response],
    policy: RetryPolicy,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    """In-process retries for short synchronous calls.

    Long waits belong in Celery retries (ResilientTask), not in a blocking loop.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            response = request()
            classify(response)
            return response
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            error: TransientError = TransientError(type(exc).__name__)
        except TransientError as exc:
            error = exc
        if not policy.should_retry(attempt):
            raise error
        sleep(policy.delay_for(attempt))
