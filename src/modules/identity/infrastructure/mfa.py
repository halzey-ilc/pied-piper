"""TOTP multi-factor authentication backed by the ORM.

The TOTP secret is encrypted at rest with Fernet (AES-128-CBC + HMAC). The key
is ``MFA.ENCRYPTION_KEY`` when configured, otherwise derived deterministically
from ``SECRET_KEY`` (rotating the Django secret therefore invalidates enrolments,
which is acceptable and documented). Recovery codes are stored only as SHA-256
hashes and are single-use.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.utils import timezone

from modules.identity.domain import totp
from modules.identity.infrastructure.models import MfaDevice, MfaRecoveryCode


@dataclass(frozen=True)
class Enrolment:
    secret: str
    provisioning_uri: str


def _fernet() -> Fernet:
    configured = settings.MFA.get("ENCRYPTION_KEY")
    if configured:
        return Fernet(configured.encode() if isinstance(configured, str) else configured)
    # Derive a stable Fernet key from the Django secret key.
    digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().lower().encode()).hexdigest()


UserId = UUID | str


class MfaService:
    def is_enabled(self, user_id: UserId) -> bool:
        return MfaDevice.objects.filter(user_id=user_id, confirmed=True).exists()

    def has_pending(self, user_id: UserId) -> bool:
        return MfaDevice.objects.filter(user_id=user_id).exists()

    def begin_enrolment(self, *, user_id: UserId, account_name: str) -> Enrolment:
        """Create (or replace) an unconfirmed device and return the secret to show once."""
        secret = totp.generate_secret()
        token = _fernet().encrypt(secret.encode())
        MfaDevice.objects.filter(user_id=user_id).delete()
        MfaDevice.objects.create(user_id=user_id, encrypted_secret=token, confirmed=False)
        uri = totp.provisioning_uri(
            secret, account_name=account_name, issuer=str(settings.MFA["ISSUER"])
        )
        return Enrolment(secret=secret, provisioning_uri=uri)

    def confirm(self, *, user_id: UserId, code: str) -> list[str] | None:
        """Confirm enrolment with a valid code; returns one-time recovery codes."""
        device = MfaDevice.objects.filter(user_id=user_id, confirmed=False).first()
        if device is None:
            return None
        if not totp.verify(self._secret(device), code):
            return None
        now = timezone.now()
        device.confirmed = True
        device.confirmed_at = now
        device.last_used_at = now
        device.save(update_fields=["confirmed", "confirmed_at", "last_used_at"])
        codes = totp.generate_recovery_codes()
        MfaRecoveryCode.objects.bulk_create(
            [MfaRecoveryCode(device=device, code_hash=_hash_code(c)) for c in codes]
        )
        return codes

    def verify(self, *, user_id: UserId, code: str) -> bool:
        """Accept a current TOTP code or an unused recovery code (consuming it)."""
        device = MfaDevice.objects.filter(user_id=user_id, confirmed=True).first()
        if device is None:
            return False
        if totp.verify(self._secret(device), code):
            device.last_used_at = timezone.now()
            device.save(update_fields=["last_used_at"])
            return True
        updated = MfaRecoveryCode.objects.filter(
            device=device, code_hash=_hash_code(code), used_at__isnull=True
        ).update(used_at=timezone.now())
        return updated == 1

    def disable(self, *, user_id: UserId) -> None:
        MfaDevice.objects.filter(user_id=user_id).delete()

    def _secret(self, device: MfaDevice) -> str:
        try:
            return _fernet().decrypt(bytes(device.encrypted_secret)).decode()
        except InvalidToken:  # pragma: no cover - key mismatch after rotation
            return ""
