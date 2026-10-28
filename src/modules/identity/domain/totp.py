"""Time-based one-time passwords (RFC 6238) and recovery codes — pure Python.

No third-party dependency: a TOTP is an HOTP (RFC 4226) over the current time
step. Verification allows a small window of adjacent steps to tolerate clock
skew. Nothing here touches Django, the ORM or settings.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

_DIGITS = 6
_PERIOD_SECONDS = 30
_B32_SECRET_BYTES = 20  # 160-bit secret, the RFC 6238 recommendation


def generate_secret() -> str:
    """A fresh base32 TOTP secret (no padding), suitable for an authenticator app."""
    return base64.b32encode(secrets.token_bytes(_B32_SECRET_BYTES)).decode("ascii").rstrip("=")


def _hotp(secret: str, counter: int) -> str:
    key = base64.b32decode(_pad_b32(secret).upper())
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    truncated = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10**_DIGITS)).zfill(_DIGITS)


def _pad_b32(secret: str) -> str:
    padding = (-len(secret)) % 8
    return secret + ("=" * padding)


def generate(secret: str, *, now: float | None = None) -> str:
    """The current TOTP code for ``secret`` (what an authenticator app would show)."""
    counter = int((now if now is not None else time.time()) // _PERIOD_SECONDS)
    return _hotp(secret, counter)


def verify(secret: str, code: str, *, now: float | None = None, window: int = 1) -> bool:
    """True if ``code`` is valid for ``secret`` within +/- ``window`` time steps.

    Comparison is constant-time. ``window=1`` accepts the previous, current and
    next 30-second step (≈90s tolerance).
    """
    candidate = code.strip().replace(" ", "")
    if len(candidate) != _DIGITS or not candidate.isdigit():
        return False
    counter = int((now if now is not None else time.time()) // _PERIOD_SECONDS)
    return any(
        hmac.compare_digest(candidate, _hotp(secret, counter + drift))
        for drift in range(-window, window + 1)
    )


def provisioning_uri(secret: str, *, account_name: str, issuer: str) -> str:
    """An ``otpauth://`` URI for QR-code enrolment in an authenticator app."""
    label = quote(f"{issuer}:{account_name}")
    params = urlencode(
        {
            "secret": secret,
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": _DIGITS,
            "period": _PERIOD_SECONDS,
        }
    )
    return f"otpauth://totp/{label}?{params}"


def generate_recovery_codes(count: int = 10) -> list[str]:
    """One-time recovery codes in ``xxxxx-xxxxx`` form (unambiguous alphabet)."""
    alphabet = "23456789abcdefghjkmnpqrstuvwxyz"  # no 0/1/l/i/o
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(alphabet) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes
