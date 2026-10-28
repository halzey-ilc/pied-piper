"""OpenID Connect relying party: verify an IdP-issued ID token.

The token signature is checked against the IdP's published JWKS (RS256), and the
issuer, audience and expiry are validated. The JWKS lookup is behind a small
protocol so tests can supply a key directly without network access; production
uses PyJWT's ``PyJWKClient`` (which caches keys).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import jwt
from django.conf import settings

from shared.domain.errors import DomainError


class OidcError(DomainError):
    code = "oidc_error"


@dataclass(frozen=True)
class OidcIdentity:
    subject: str
    email: str


class SigningKeyResolver(Protocol):
    def get_key(self, kid: str | None) -> Any:
        """Return a key object/PEM that ``jwt.decode`` accepts for the given kid."""


class JwksClientResolver:
    """Resolves keys from a remote JWKS URI (cached by PyJWKClient)."""

    def __init__(self, jwks_uri: str) -> None:
        self._client = jwt.PyJWKClient(jwks_uri)

    def get_key(self, kid: str | None) -> Any:
        if not kid:
            raise OidcError("The ID token has no key id.")
        return self._client.get_signing_key(kid).key


class OidcVerifier:
    def __init__(self, *, issuer: str, client_id: str, resolver: SigningKeyResolver) -> None:
        self._issuer = issuer
        self._client_id = client_id
        self._resolver = resolver

    def verify(self, id_token: str) -> OidcIdentity:
        try:
            header = jwt.get_unverified_header(id_token)
            key = self._resolver.get_key(header.get("kid"))
            claims = jwt.decode(
                id_token,
                key,
                algorithms=["RS256"],
                audience=self._client_id,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except OidcError:
            raise
        except jwt.PyJWTError as exc:
            # Never leak token internals; the reason is logged elsewhere if needed.
            raise OidcError("The ID token could not be verified.") from exc

        email = claims.get("email")
        if not email or claims.get("email_verified") is False:
            raise OidcError("The ID token has no verified email claim.")
        return OidcIdentity(subject=str(claims["sub"]), email=str(email))


def verifier_from_settings() -> OidcVerifier:
    """Build the production verifier from ``settings.OIDC``; raises if disabled."""
    cfg = settings.OIDC
    if not cfg["ENABLED"] or not (cfg["ISSUER"] and cfg["CLIENT_ID"] and cfg["JWKS_URI"]):
        raise OidcError("OIDC login is not configured.")
    return OidcVerifier(
        issuer=cfg["ISSUER"],
        client_id=cfg["CLIENT_ID"],
        resolver=JwksClientResolver(cfg["JWKS_URI"]),
    )
