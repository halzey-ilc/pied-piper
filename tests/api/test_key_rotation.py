"""Graceful rotation of the JWT signing key and the MFA encryption key."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from cryptography.fernet import Fernet
from rest_framework.test import APIClient
from rest_framework_simplejwt.backends import TokenBackend
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import AccessToken

from modules.identity.domain import totp
from modules.identity.infrastructure.mfa import MfaService

pytestmark = pytest.mark.django_db

# A previous signing key, different from the current (test) SIGNING_KEY.
PREVIOUS_KEY = "previous-jwt-signing-key-" + "x" * 40
UNRELATED_KEY = "unrelated-jwt-signing-key-" + "z" * 40


def _bearer(token: str) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def _token_signed_with(key: str, user: Any) -> str:
    """A well-formed access token (valid claims) signed with an arbitrary key."""
    payload = AccessToken.for_user(user).payload
    backend = TokenBackend(
        api_settings.ALGORITHM, key, "", api_settings.AUDIENCE, api_settings.ISSUER
    )
    return backend.encode(payload)


def test_token_signed_with_previous_key_is_accepted_during_rotation(
    make_user: Callable[..., Any], settings: Any
) -> None:
    user = make_user("rotator")
    old_token = _token_signed_with(PREVIOUS_KEY, user)

    # During rotation the previous key is listed as a verification fallback.
    settings.JWT_SIGNING_KEY_FALLBACKS = [PREVIOUS_KEY]
    assert _bearer(old_token).get("/api/v1/me/").status_code == 200

    # Once the fallback is removed the old token is rejected.
    settings.JWT_SIGNING_KEY_FALLBACKS = []
    assert _bearer(old_token).get("/api/v1/me/").status_code == 401


def test_token_signed_with_unknown_key_is_rejected(
    make_user: Callable[..., Any], settings: Any
) -> None:
    user = make_user("stranger")
    foreign_token = _token_signed_with(UNRELATED_KEY, user)
    settings.JWT_SIGNING_KEY_FALLBACKS = [PREVIOUS_KEY]  # not the key that signed it
    assert _bearer(foreign_token).get("/api/v1/me/").status_code == 401


def test_mfa_encryption_key_rotation(make_user: Callable[..., Any], settings: Any) -> None:
    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()
    settings.MFA = {**settings.MFA, "ENCRYPTION_KEY": key_a, "ENCRYPTION_KEY_FALLBACKS": []}

    user = make_user("mfarot")
    svc = MfaService()
    enrolment = svc.begin_enrolment(user_id=user.pk, account_name="mfarot")
    assert svc.confirm(user_id=user.pk, code=totp.generate(enrolment.secret)) is not None

    # Rotate: B is primary, A still accepted for decryption.
    settings.MFA = {**settings.MFA, "ENCRYPTION_KEY": key_b, "ENCRYPTION_KEY_FALLBACKS": [key_a]}
    assert svc.verify(user_id=user.pk, code=totp.generate(enrolment.secret)) is True

    # Re-encrypt under B, then drop the fallback entirely.
    assert svc.reencrypt_all() == 1
    settings.MFA = {**settings.MFA, "ENCRYPTION_KEY": key_b, "ENCRYPTION_KEY_FALLBACKS": []}
    assert svc.verify(user_id=user.pk, code=totp.generate(enrolment.secret)) is True
