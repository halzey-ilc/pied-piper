"""Pure TOTP / recovery-code domain (RFC 6238)."""

from __future__ import annotations

import re

from modules.identity.domain import totp


def test_generated_code_verifies_at_same_time() -> None:
    secret = totp.generate_secret()
    code = totp.generate(secret, now=1_000_000)
    assert totp.verify(secret, code, now=1_000_000)


def test_adjacent_window_is_accepted_but_far_drift_is_not() -> None:
    secret = totp.generate_secret()
    code = totp.generate(secret, now=1_000_000)
    assert totp.verify(secret, code, now=1_000_000 + 30)  # next step, within window
    assert not totp.verify(secret, code, now=1_000_000 + 300)  # far away


def test_malformed_input_rejected() -> None:
    secret = totp.generate_secret()
    assert not totp.verify(secret, "12ab56", now=1_000_000)  # non-digit
    assert not totp.verify(secret, "1234567", now=1_000_000)  # wrong length


def test_rfc6238_appendix_b_sha1_vector() -> None:
    # RFC 6238 vector: ASCII secret "12345678901234567890" at T=59 -> 94287082,
    # truncated to 6 digits -> 287082.
    import base64

    secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
    assert totp.generate(secret, now=59) == "287082"


def test_provisioning_uri_has_required_params() -> None:
    uri = totp.provisioning_uri("ABCDEF", account_name="demo", issuer="CRM")
    assert uri.startswith("otpauth://totp/CRM%3Ademo?")  # label is percent-encoded
    assert "secret=ABCDEF" in uri and "issuer=CRM" in uri and "period=30" in uri


def test_recovery_codes_are_unique_and_formatted() -> None:
    codes = totp.generate_recovery_codes(8)
    assert len(codes) == 8 and len(set(codes)) == 8
    assert all(re.fullmatch(r"[23456789abcdefghjkmnpqrstuvwxyz]{5}-[a-z2-9]{5}", c) for c in codes)
