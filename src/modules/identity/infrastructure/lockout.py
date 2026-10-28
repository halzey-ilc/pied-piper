"""Account lockout: throttle repeated failed logins per username.

State is a single row per submitted username. Failures accumulate within a
sliding window; once they reach the threshold the username is locked for a fixed
duration. A successful login clears the row. Keyed by username string so that
attempts against unknown accounts are limited too and no row reveals whether an
account exists.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from modules.identity.infrastructure.models import LoginState


class LoginLockout:
    def __init__(self) -> None:
        cfg = settings.LOGIN_LOCKOUT
        self._max_failures: int = cfg["MAX_FAILURES"]
        self._lockout = timedelta(minutes=cfg["LOCKOUT_MINUTES"])
        self._window = timedelta(minutes=cfg["WINDOW_MINUTES"])

    def locked_seconds_remaining(self, username: str) -> int | None:
        """Seconds until the lock lifts, or ``None`` when not locked."""
        state = LoginState.objects.filter(pk=username).first()
        if state is None or state.locked_until is None:
            return None
        remaining = (state.locked_until - timezone.now()).total_seconds()
        return int(remaining) + 1 if remaining > 0 else None

    def record_failure(self, username: str) -> None:
        now = timezone.now()
        with transaction.atomic(using="default"):
            state, _ = LoginState.objects.select_for_update().get_or_create(pk=username)
            # Reset the counter if the window has elapsed since the first failure.
            if state.first_failure_at is None or now - state.first_failure_at > self._window:
                state.failure_count = 0
                state.first_failure_at = now
            state.failure_count += 1
            if state.failure_count >= self._max_failures:
                state.locked_until = now + self._lockout
            state.save()

    def record_success(self, username: str) -> None:
        LoginState.objects.filter(pk=username).delete()
