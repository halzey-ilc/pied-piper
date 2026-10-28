"""OIDC ID-token verification (no network: the signing key is injected)."""

from __future__ import annotations

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from modules.identity.infrastructure.oidc import OidcError, OidcVerifier

ISSUER = "https://idp.example.com"
CLIENT_ID = "crm-client"


class _StaticResolver:
    def __init__(self, public_key: Any) -> None:
        self._public_key = public_key

    def get_key(self, kid: str | None) -> Any:
        return self._public_key


@pytest.fixture(scope="module")
def keypair() -> Any:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _token(private_key: Any, **overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "idp-user-123",
        "email": "person@example.com",
        "email_verified": True,
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "k1"})


def _verifier(public_key: Any) -> OidcVerifier:
    return OidcVerifier(issuer=ISSUER, client_id=CLIENT_ID, resolver=_StaticResolver(public_key))


def test_valid_token_yields_identity(keypair: Any) -> None:
    private_key, public_key = keypair
    identity = _verifier(public_key).verify(_token(private_key))
    assert identity.subject == "idp-user-123"
    assert identity.email == "person@example.com"


def test_wrong_audience_is_rejected(keypair: Any) -> None:
    private_key, public_key = keypair
    with pytest.raises(OidcError):
        _verifier(public_key).verify(_token(private_key, aud="someone-else"))


def test_expired_token_is_rejected(keypair: Any) -> None:
    private_key, public_key = keypair
    with pytest.raises(OidcError):
        _verifier(public_key).verify(_token(private_key, exp=int(time.time()) - 10))


def test_unverified_email_is_rejected(keypair: Any) -> None:
    private_key, public_key = keypair
    with pytest.raises(OidcError):
        _verifier(public_key).verify(_token(private_key, email_verified=False))


def test_token_signed_by_other_key_is_rejected(keypair: Any) -> None:
    _, public_key = keypair
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(OidcError):
        _verifier(public_key).verify(_token(attacker))
