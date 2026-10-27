from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from shared.domain.errors import DomainError, DomainValidationError


class Provider(StrEnum):
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    INSTAGRAM = "instagram"
    TELEPHONY = "telephony"


@dataclass(frozen=True, slots=True)
class ExternalReference:
    """Identifier of an object in an external system (e.g. a WhatsApp message id)."""

    provider: Provider
    external_id: str

    def __post_init__(self) -> None:
        if not self.external_id or len(self.external_id) > 255:
            raise DomainValidationError("External id must be 1-255 characters.")


class IntegrationNotConfiguredError(DomainError):
    code = "integration_not_configured"


class InvalidSignatureError(DomainError):
    code = "invalid_signature"
