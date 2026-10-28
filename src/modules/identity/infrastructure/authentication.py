"""JWT authentication that tolerates signing-key rotation.

New tokens are always signed with the current ``SIGNING_KEY``. During a rotation
window the previous key(s) listed in ``JWT_SIGNING_KEY_FALLBACKS`` are still
accepted for *verification only*, so access and refresh tokens minted before the
rotation keep working until they expire. With no fallbacks configured this
behaves exactly like the stock ``JWTAuthentication``.
"""

from __future__ import annotations

from typing import Any, cast

from django.conf import settings
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.backends import TokenBackend
from rest_framework_simplejwt.exceptions import InvalidToken, TokenBackendError, TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import Token


class FallbackJWTAuthentication(JWTAuthentication):
    def get_validated_token(self, raw_token: bytes) -> Token:
        try:
            return super().get_validated_token(raw_token)
        except InvalidToken:
            fallbacks: list[str] = settings.JWT_SIGNING_KEY_FALLBACKS
            if not fallbacks:
                raise
            for key in fallbacks:
                if self._signature_valid(raw_token, key):
                    return self._accept(raw_token)
            raise

    def _signature_valid(self, raw_token: bytes, key: str) -> bool:
        backend = TokenBackend(
            api_settings.ALGORITHM,
            key,
            api_settings.VERIFYING_KEY or "",
            api_settings.AUDIENCE,
            api_settings.ISSUER,
            leeway=api_settings.LEEWAY,
        )
        try:
            backend.decode(cast(Any, raw_token), verify=True)  # signature + exp + aud + iss
        except (TokenError, TokenBackendError):
            return False
        return True

    def _accept(self, raw_token: bytes) -> Token:
        # The signature has been verified against a fallback key above; build the
        # token object without re-checking the signature, then enforce the claim
        # checks (expiry, token type).
        for token_class in api_settings.AUTH_TOKEN_CLASSES:
            try:
                token: Token = token_class(raw_token, verify=False)
                token.verify()
                return token
            except TokenError:
                continue
        raise InvalidToken("Token is not valid for any accepted type.")
