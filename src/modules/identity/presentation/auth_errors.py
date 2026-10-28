"""Auth-specific API exceptions, mapped to safe response bodies by the handler."""

from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import APIException, AuthenticationFailed


class AccountLocked(APIException):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_detail = "Too many failed attempts. Try again later."
    default_code = "account_locked"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__()
        self.retry_after_seconds = retry_after_seconds


class MfaRequired(APIException):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "A one-time code is required. Resend with the 'otp' field."
    default_code = "mfa_required"


class InvalidOtp(APIException):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "The one-time code is invalid or expired."
    default_code = "invalid_otp"


class OidcLoginFailed(AuthenticationFailed):
    default_detail = "The ID token could not be verified."
    default_code = "oidc_error"


class OidcNoAccount(AuthenticationFailed):
    default_detail = "No account is linked to this identity."
    default_code = "oidc_no_account"
