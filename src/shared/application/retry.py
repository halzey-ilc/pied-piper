"""Retry policy: exponential backoff with full jitter."""

from __future__ import annotations

import random
from dataclasses import dataclass, field


class TransientError(Exception):
    """A failure that may succeed when retried (timeouts, 5xx, broker hiccups)."""


class PermanentError(Exception):
    """A failure that must not be retried (validation, 4xx, bad signature)."""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 300.0
    rng: random.Random = field(default_factory=random.Random, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.base_delay_seconds <= 0 or self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("invalid delay bounds")

    def should_retry(self, attempt: int) -> bool:
        """``attempt`` is the number of attempts already made (1-based)."""
        return attempt < self.max_attempts

    def backoff_ceiling(self, attempt: int) -> float:
        exponent = max(attempt - 1, 0)
        return float(min(self.max_delay_seconds, self.base_delay_seconds * (2**exponent)))

    def delay_for(self, attempt: int) -> float:
        """Full jitter: uniform in [0, ceiling] to avoid synchronized retry storms."""
        return self.rng.uniform(0.0, self.backoff_ceiling(attempt))
