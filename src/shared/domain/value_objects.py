"""Reusable value objects."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from shared.domain.errors import DomainValidationError

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,253}\.[^@\s]{2,63}$")
_PHONE_RE = re.compile(r"^\+[1-9]\d{6,14}$")  # E.164
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")  # ISO 4217 shape (not validated against a list)
_MAX_MONEY = Decimal("1000000000000.00")  # 1e12, guards against absurd values


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


@dataclass(frozen=True, slots=True)
class Money:
    """A non-negative monetary amount in a single currency (ISO-4217 code shape)."""

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        try:
            amount = Decimal(self.amount).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError):
            raise DomainValidationError("Amount is not a valid number.") from None
        if amount < 0:
            raise DomainValidationError("Amount must not be negative.")
        if amount > _MAX_MONEY:
            raise DomainValidationError("Amount is too large.")
        currency = self.currency.strip().upper()
        if not _CURRENCY_RE.match(currency):
            raise DomainValidationError("Currency must be a 3-letter code, e.g. USD or KZT.")
        object.__setattr__(self, "amount", amount)
        object.__setattr__(self, "currency", currency)

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"
