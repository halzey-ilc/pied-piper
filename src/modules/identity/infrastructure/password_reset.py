"""Self-service password reset: issue a one-time token and consume it.

The token is random and high-entropy; only its SHA-256 hash is stored, it expires
after a configured window and is single-use. The request step never reveals
whether an email is registered. A successful reset also clears any login-lockout
state for the account.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from modules.identity.infrastructure.models import LoginState, PasswordResetToken
from shared.domain.errors import DomainValidationError

logger = logging.getLogger(__name__)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class PasswordResetService:
    def request_reset(self, email: str) -> None:
        """Issue and email a reset token. Silent when no active account matches."""
        user_model = get_user_model()
        user = user_model.objects.filter(email__iexact=email, is_active=True).first()
        if user is None or not user.email:
            logger.info("password_reset_requested_unknown")  # no email echoed
            return

        raw_token = secrets.token_urlsafe(32)
        ttl = timedelta(minutes=settings.PASSWORD_RESET["TTL_MINUTES"])
        with transaction.atomic(using="default"):
            # Only one live token per user.
            PasswordResetToken.objects.filter(user=user, used_at__isnull=True).delete()
            PasswordResetToken.objects.create(
                user=user,
                token_hash=_hash_token(raw_token),
                expires_at=timezone.now() + ttl,
            )
        self._send_email(user.email, raw_token)
        logger.info("password_reset_requested", extra={"user_id": str(user.pk)})

    def confirm_reset(self, token: str, new_password: str) -> None:
        now = timezone.now()
        reset = (
            PasswordResetToken.objects.select_related("user")
            .filter(token_hash=_hash_token(token), used_at__isnull=True, expires_at__gt=now)
            .first()
        )
        if reset is None:
            raise DomainValidationError("The reset link is invalid or has expired.")

        user = reset.user
        try:
            validate_password(new_password, user)
        except DjangoValidationError as exc:
            raise DomainValidationError(" ".join(exc.messages)) from None

        with transaction.atomic(using="default"):
            user.set_password(new_password)
            user.save(update_fields=["password"])
            # Burn this token and any siblings; unlock the account.
            PasswordResetToken.objects.filter(user=user).update(used_at=now)
            LoginState.objects.filter(pk=user.get_username()).delete()
        logger.info("password_reset_completed", extra={"user_id": str(user.pk)})

    def _send_email(self, email: str, raw_token: str) -> None:
        link = f"{settings.PASSWORD_RESET['URL_BASE']}?token={raw_token}"
        minutes = settings.PASSWORD_RESET["TTL_MINUTES"]
        send_mail(
            subject="Reset your CRM password",
            message=(
                "We received a request to reset your password.\n\n"
                f"Use this link within {minutes} minutes to choose a new password:\n"
                f"{link}\n\n"
                "If you did not request this, you can ignore this email."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
