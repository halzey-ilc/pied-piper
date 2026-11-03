"""Website intake keys and the submissions they accept. Pure Python.

An intake key belongs to one department of one company. Its token is put into the
company's website form: anyone who knows it can submit a lead (it is a public form id,
not a credential), so the controls are revocation, throttling and validation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from shared.domain.errors import DomainValidationError

MAX_KEY_NAME_LENGTH = 100
MAX_ACTIVE_KEYS_PER_DEPARTMENT = 20

MAX_NAME_LENGTH = 200
MAX_PHONE_LENGTH = 32
MAX_EMAIL_LENGTH = 254
MAX_COMMENT_LENGTH = 2000
MAX_URL_LENGTH = 500
MAX_UTM_LENGTH = 200

# secrets.token_urlsafe(24) gives 32 characters; anything else is not one of ours.
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")


def is_token_shaped(token: str) -> bool:
    return bool(TOKEN_RE.match(token))


def clean_key_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise DomainValidationError("Key name must not be empty.")
    if len(cleaned) > MAX_KEY_NAME_LENGTH:
        raise DomainValidationError(f"Key name must be at most {MAX_KEY_NAME_LENGTH} characters.")
    return cleaned


@dataclass(frozen=True, slots=True)
class IntakeKey:
    id: UUID
    tenant_id: UUID
    department_id: UUID
    name: str
    token: str
    created_by: UUID
    created_at: datetime
    revoked_at: datetime | None = None
    revoked_by: UUID | None = None
    last_used_at: datetime | None = None
    leads_count: int = 0

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


@dataclass(frozen=True, slots=True)
class WebsiteSubmission:
    """What a website visitor sent. Lengths are checked here; the lead itself validates
    the e-mail and phone formats when it is captured."""

    name: str
    phone: str | None = None
    email: str | None = None
    comment: str | None = None
    page_url: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None

    def __post_init__(self) -> None:
        if not " ".join(self.name.split()):
            raise DomainValidationError("Name is required.")
        if not self.phone and not self.email:
            raise DomainValidationError("Give a phone number or an e-mail.")
        limits = (
            ("name", self.name, MAX_NAME_LENGTH),
            ("phone", self.phone, MAX_PHONE_LENGTH),
            ("email", self.email, MAX_EMAIL_LENGTH),
            ("comment", self.comment, MAX_COMMENT_LENGTH),
            ("page_url", self.page_url, MAX_URL_LENGTH),
            ("utm_source", self.utm_source, MAX_UTM_LENGTH),
            ("utm_medium", self.utm_medium, MAX_UTM_LENGTH),
            ("utm_campaign", self.utm_campaign, MAX_UTM_LENGTH),
        )
        for label, value, limit in limits:
            if value is not None and len(value) > limit:
                raise DomainValidationError(f"{label} must be at most {limit} characters.")

    def fingerprint(self) -> str:
        """Contact identity used to recognise a repeated submission (digits of the phone,
        lower-cased e-mail)."""
        phone = re.sub(r"\D", "", self.phone or "")
        email = (self.email or "").strip().lower()
        return f"{phone}|{email}"

    def note(self) -> str | None:
        """Text of the note attached to the lead: the comment and where it came from."""
        lines: list[str] = []
        if self.comment and self.comment.strip():
            lines.append(self.comment.strip())
        extras = (
            ("Страница", self.page_url),
            ("utm_source", self.utm_source),
            ("utm_medium", self.utm_medium),
            ("utm_campaign", self.utm_campaign),
        )
        details = [
            f"{label}: {value.strip()}" for label, value in extras if value and value.strip()
        ]
        if details:
            if lines:
                lines.append("")
            lines.extend(details)
        return "\n".join(lines) or None


# Country -> (calling code, national trunk prefix, national number length without it).
# Website visitors type local numbers («0700 123 456», «8 701 123 45 67»); leads keep E.164.
_NATIONAL_FORMATS: dict[str, tuple[str, str, int]] = {
    "KG": ("996", "0", 9),
    "KZ": ("7", "8", 10),
    "RU": ("7", "8", 10),
    "UZ": ("998", "", 9),
}


def international_phone(raw: str | None, country: str) -> str | None:
    """A visitor's phone in E.164 when it can be read for the company's country; otherwise
    the input unchanged, so lead capture reports the format error as usual."""
    digits = re.sub(r"[\s\-().]", "", raw or "")
    if digits.startswith("+"):
        return digits
    if digits.startswith("00"):
        return "+" + digits[2:]
    national = _NATIONAL_FORMATS.get(country)
    if not digits.isdigit() or national is None:
        return raw
    code, trunk, length = national
    # (prefix the visitor typed, digits that replace it): «996…», «0…»/«8…», bare number
    readings = [(code, code), (trunk, code)] if trunk else [(code, code)]
    readings.append(("", code))
    for typed, replacement in readings:
        if len(digits) == len(typed) + length and digits.startswith(typed):
            return "+" + replacement + digits[len(typed) :]
    return raw
