"""Serializers for hardened authentication (lockout + MFA) and MFA management."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from modules.identity.infrastructure.lockout import LoginLockout
from modules.identity.infrastructure.mfa import MfaService
from modules.identity.presentation.auth_errors import AccountLocked, InvalidOtp, MfaRequired


class HardenedTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Username + password, with account lockout and an optional MFA step."""

    otp = serializers.CharField(
        required=False,
        allow_blank=True,
        write_only=True,
        help_text="One-time TOTP code or a recovery code, required when MFA is enabled.",
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        username = str(attrs.get(self.username_field, ""))
        lockout = LoginLockout()
        remaining = lockout.locked_seconds_remaining(username)
        if remaining is not None:
            raise AccountLocked(remaining)

        try:
            data: dict[str, Any] = super().validate(attrs)
        except AuthenticationFailed:
            lockout.record_failure(username)
            raise

        mfa = MfaService()
        assert self.user is not None  # noqa: S101  # nosec B101 - set by super().validate
        user_pk = self.user.pk
        if mfa.is_enabled(user_pk):
            otp = str(self.initial_data.get("otp", "")).strip()
            if not otp:
                raise MfaRequired()
            if not mfa.verify(user_id=user_pk, code=otp):
                lockout.record_failure(username)
                raise InvalidOtp()

        lockout.record_success(username)
        return data


class MfaConfirmSerializer(serializers.Serializer[dict[str, Any]]):
    code = serializers.CharField(help_text="The 6-digit code from your authenticator app.")


class MfaDisableSerializer(serializers.Serializer[dict[str, Any]]):
    password = serializers.CharField(help_text="Your current password, to confirm.")


class OidcLoginSerializer(serializers.Serializer[dict[str, Any]]):
    id_token = serializers.CharField(help_text="The ID token issued by the OIDC provider.")


class PasswordResetRequestSerializer(serializers.Serializer[dict[str, Any]]):
    email = serializers.EmailField(help_text="The account's email address.")


class PasswordResetConfirmSerializer(serializers.Serializer[dict[str, Any]]):
    token = serializers.CharField(help_text="The token from the reset email.")
    new_password = serializers.CharField(help_text="The new password to set.")


class PasswordChangeSerializer(serializers.Serializer[dict[str, Any]]):
    current_password = serializers.CharField(help_text="Your current (or temporary) password.")
    new_password = serializers.CharField(help_text="The new password.")


class LogoutSerializer(serializers.Serializer[dict[str, Any]]):
    refresh = serializers.CharField(help_text="The refresh token to revoke.")


class TokenPairResponseSerializer(serializers.Serializer[dict[str, Any]]):
    access = serializers.CharField()
    refresh = serializers.CharField()


class EnrolmentResponseSerializer(serializers.Serializer[dict[str, Any]]):
    secret = serializers.CharField()
    otpauth_uri = serializers.CharField()


class RecoveryCodesResponseSerializer(serializers.Serializer[dict[str, Any]]):
    recovery_codes = serializers.ListField(child=serializers.CharField())
