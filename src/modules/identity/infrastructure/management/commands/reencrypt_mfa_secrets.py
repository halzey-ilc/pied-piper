"""Re-encrypt all stored TOTP secrets under the current primary MFA key.

Run after rotating ``MFA_ENCRYPTION_KEY`` (old key moved to
``MFA_ENCRYPTION_KEY_FALLBACKS``). Once this completes successfully the fallback
key can be removed. Safe to run repeatedly.

    python manage.py reencrypt_mfa_secrets
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from modules.identity.infrastructure.mfa import MfaService


class Command(BaseCommand):
    help = "Re-encrypt stored MFA secrets under the current primary encryption key."

    def handle(self, *args: Any, **options: Any) -> None:
        count = MfaService().reencrypt_all()
        self.stdout.write(self.style.SUCCESS(f"Re-encrypted {count} MFA device secret(s)."))
