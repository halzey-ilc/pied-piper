"""Revoking a user's credentials when an administrator resets or deactivates them."""

from __future__ import annotations

from uuid import UUID

from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from modules.identity.infrastructure.models import PasswordResetToken

DB = "default"


def revoke_credentials(user_id: UUID) -> int:
    """Blacklist every refresh token of the user and expire unused password-reset links.

    Access tokens already issued stay valid only until their short TTL; a deactivated
    user is refused even then (the authentication checks ``is_active``). Returns the
    number of refresh tokens revoked.
    """
    revoked = 0
    for outstanding in OutstandingToken.objects.using(DB).filter(user_id=user_id):
        _, created = BlacklistedToken.objects.using(DB).get_or_create(token=outstanding)
        revoked += int(created)
    PasswordResetToken.objects.using(DB).filter(user_id=user_id, used_at__isnull=True).update(
        used_at=timezone.now()
    )
    return revoked
