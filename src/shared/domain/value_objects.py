"""Reusable value objects."""

from __future__ import annotations

import re
from dataclasses import dataclass

from shared.domain.errors import DomainValidationError

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,253}\.[^@\s]{2,63}$")
_PHONE_RE = re.compile(r"^\+[1-9]\d{6,14}$")  # E.164


@dataclass(frozen=True, slots=True)
class EmailAddress:
    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().lower()
        if len(normalized) > 254 or not _EMAIL_RE.match(normalized):
            raise DomainValidationError("Invalid e-mail address.")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class PhoneNumber:
    """Phone number in E.164 format, e.g. +77011234567."""

    value: str

    def __post_init__(self) -> None:
        normalized = re.sub(r"[\s\-()]", "", self.value)
        if not _PHONE_RE.match(normalized):
            raise DomainValidationError("Phone number must be in E.164 format, e.g. +77011234567.")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value
