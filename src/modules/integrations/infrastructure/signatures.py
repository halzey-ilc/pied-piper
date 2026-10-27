"""Webhook signature verifiers built on the standard library ``hmac`` module.

No custom cryptography: HMAC-SHA256 and constant-time comparison only.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable, Mapping


def _header(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


class HmacSha256Verifier:
    """``<header>: sha256=<hex hmac(secret, raw body)>`` (Meta: WhatsApp / Instagram)."""

    def __init__(
        self, secret: str, header: str = "X-Hub-Signature-256", prefix: str = "sha256="
    ) -> None:
        if not secret:
            raise ValueError("secret must not be empty")
        self._secret = secret.encode()
        self._header = header
        self._prefix = prefix

    def verify(self, headers: Mapping[str, str], body: bytes) -> bool:
        supplied = _header(headers, self._header)
        if not supplied or not supplied.startswith(self._prefix):
            return False
        expected = hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(supplied[len(self._prefix) :], expected)


class SharedSecretHeaderVerifier:
    """Static secret echoed in a header (Telegram ``X-Telegram-Bot-Api-Secret-Token``)."""

    def __init__(self, secret: str, header: str = "X-Telegram-Bot-Api-Secret-Token") -> None:
        if not secret:
            raise ValueError("secret must not be empty")
        self._secret = secret
        self._header = header

    def verify(self, headers: Mapping[str, str], body: bytes) -> bool:
        supplied = _header(headers, self._header)
        return supplied is not None and hmac.compare_digest(
            supplied.encode(), self._secret.encode()
        )


class TimestampedHmacVerifier:
    """HMAC over ``<timestamp>.<body>`` with a freshness window (replay protection).

    Generic scheme for telephony/other providers that we define ourselves.
    """

    def __init__(
        self,
        secret: str,
        *,
        signature_header: str = "X-Signature",
        timestamp_header: str = "X-Signature-Timestamp",
        tolerance_seconds: int = 300,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not secret:
            raise ValueError("secret must not be empty")
        self._secret = secret.encode()
        self._sig_header = signature_header
        self._ts_header = timestamp_header
        self._tolerance = tolerance_seconds
        self._clock = clock

    @staticmethod
    def sign(secret: str, timestamp: str, body: bytes) -> str:
        return hmac.new(
            secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest()

    def verify(self, headers: Mapping[str, str], body: bytes) -> bool:
        signature = _header(headers, self._sig_header)
        timestamp = _header(headers, self._ts_header)
        if not signature or not timestamp or not timestamp.isdigit():
            return False
        if abs(self._clock() - int(timestamp)) > self._tolerance:
            return False
        expected = hmac.new(
            self._secret, timestamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature, expected)
