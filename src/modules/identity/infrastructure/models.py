from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Global user account (control plane). Tenant access is granted via memberships."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)

    class Meta:
        db_table = "identity_user"


class LoginState(models.Model):
    """Per-username failed-login counter used for account lockout.

    Keyed by the submitted username (not a FK) so attempts against unknown or
    not-yet-created accounts are still rate-limited, and so an existing user is
    never revealed by the presence of a row.
    """

    username = models.CharField(max_length=150, primary_key=True)
    failure_count = models.PositiveIntegerField(default=0)
    first_failure_at = models.DateTimeField(null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "identity_login_state"

    def __str__(self) -> str:
        return f"login_state:{self.username}"


class MfaDevice(models.Model):
    """A user's TOTP authenticator. The secret is stored encrypted at rest."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mfa_device"
    )
    encrypted_secret = models.BinaryField()
    confirmed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "identity_mfa_device"

    def __str__(self) -> str:
        return f"mfa_device:{self.user_id}"


class MfaRecoveryCode(models.Model):
    """A single-use recovery code (stored hashed) for when the TOTP device is lost."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device = models.ForeignKey(MfaDevice, on_delete=models.CASCADE, related_name="recovery_codes")
    code_hash = models.CharField(max_length=64, unique=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "identity_mfa_recovery_code"

    def __str__(self) -> str:
        return f"recovery_code:{self.id}"


class PasswordResetToken(models.Model):
    """A single-use, time-limited password-reset token (stored hashed)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="password_reset_tokens"
    )
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "identity_password_reset_token"
        indexes = [models.Index(fields=["user", "used_at"], name="pwreset_user_used_idx")]

    def __str__(self) -> str:
        return f"password_reset:{self.user_id}"
