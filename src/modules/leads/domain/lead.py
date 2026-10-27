"""Lead aggregate. Pure Python: no Django, no ORM.

Lifecycle (enforced here, not in the API):

    NEW ──qualify──> QUALIFIED ──convert──> CONVERTED
      │                  │
      └──disqualify──────┴──> UNQUALIFIED

A lead may be re-opened from UNQUALIFIED back to NEW. CONVERTED is terminal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID, uuid4

from shared.domain.errors import ConflictError, DomainValidationError
from shared.domain.events import DomainEvent
from shared.domain.value_objects import EmailAddress, PhoneNumber

MAX_NAME_LENGTH = 200
MAX_REASON_LENGTH = 500


class LeadStatus(StrEnum):
    NEW = "new"
    QUALIFIED = "qualified"
    UNQUALIFIED = "unqualified"
    CONVERTED = "converted"


class LeadSource(StrEnum):
    WEBSITE = "website"
    REFERRAL = "referral"
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    INSTAGRAM = "instagram"
    PHONE = "phone"
    IMPORT = "import"
    OTHER = "other"


class LeadStateError(ConflictError):
    """An operation is not allowed from the lead's current status."""

    code = "lead_invalid_state"


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise DomainValidationError("Lead name must not be empty.")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise DomainValidationError(f"Lead name must be at most {MAX_NAME_LENGTH} characters.")
    return cleaned


def _require_contact(email: EmailAddress | None, phone: PhoneNumber | None) -> None:
    if email is None and phone is None:
        raise DomainValidationError("A lead needs at least an e-mail or a phone number.")


@dataclass(frozen=True, kw_only=True)
class LeadCaptured(DomainEvent):
    event_type: ClassVar[str] = "leads.lead_captured"
    lead_id: UUID
    owner_user_id: UUID
    source: str

    def payload(self) -> dict[str, Any]:
        return {
            "lead_id": str(self.lead_id),
            "owner_user_id": str(self.owner_user_id),
            "source": self.source,
        }


@dataclass(frozen=True, kw_only=True)
class LeadQualified(DomainEvent):
    event_type: ClassVar[str] = "leads.lead_qualified"
    lead_id: UUID

    def payload(self) -> dict[str, Any]:
        return {"lead_id": str(self.lead_id)}


@dataclass(frozen=True, kw_only=True)
class LeadDisqualified(DomainEvent):
    event_type: ClassVar[str] = "leads.lead_disqualified"
    lead_id: UUID

    def payload(self) -> dict[str, Any]:
        return {"lead_id": str(self.lead_id)}


@dataclass(frozen=True, kw_only=True)
class LeadConverted(DomainEvent):
    event_type: ClassVar[str] = "leads.lead_converted"
    lead_id: UUID
    customer_id: UUID

    def payload(self) -> dict[str, Any]:
        return {"lead_id": str(self.lead_id), "customer_id": str(self.customer_id)}


@dataclass(slots=True)
class Lead:
    id: UUID
    name: str
    owner_user_id: UUID
    source: LeadSource
    status: LeadStatus = LeadStatus.NEW
    email: EmailAddress | None = None
    phone: PhoneNumber | None = None
    team_id: UUID | None = None
    disqualify_reason: str | None = None
    converted_customer_id: UUID | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    version: int = 1

    @classmethod
    def capture(
        cls,
        *,
        name: str,
        owner_user_id: UUID,
        source: LeadSource = LeadSource.OTHER,
        email: str | None = None,
        phone: str | None = None,
        team_id: UUID | None = None,
    ) -> tuple[Lead, LeadCaptured]:
        email_vo = EmailAddress(email) if email else None
        phone_vo = PhoneNumber(phone) if phone else None
        _require_contact(email_vo, phone_vo)
        lead = cls(
            id=uuid4(),
            name=_clean_name(name),
            owner_user_id=owner_user_id,
            source=source,
            email=email_vo,
            phone=phone_vo,
            team_id=team_id,
        )
        return lead, LeadCaptured(lead_id=lead.id, owner_user_id=owner_user_id, source=source.value)

    def update_details(
        self, *, name: str | None = None, email: str | None = None, phone: str | None = None
    ) -> bool:
        """Edit contact details. Not allowed once converted. Returns True if anything changed."""
        if self.status is LeadStatus.CONVERTED:
            raise LeadStateError("A converted lead can no longer be edited.")
        new_email = self.email if email is None else (EmailAddress(email) if email else None)
        new_phone = self.phone if phone is None else (PhoneNumber(phone) if phone else None)
        _require_contact(new_email, new_phone)
        changed = False
        if name is not None and (cleaned := _clean_name(name)) != self.name:
            self.name, changed = cleaned, True
        if new_email != self.email:
            self.email, changed = new_email, True
        if new_phone != self.phone:
            self.phone, changed = new_phone, True
        if changed:
            self.version += 1
        return changed

    def qualify(self) -> LeadQualified:
        if self.status not in (LeadStatus.NEW, LeadStatus.UNQUALIFIED):
            raise LeadStateError(f"A {self.status.value} lead cannot be qualified.")
        self.status = LeadStatus.QUALIFIED
        self.disqualify_reason = None
        self.version += 1
        return LeadQualified(lead_id=self.id)

    def disqualify(self, reason: str) -> LeadDisqualified:
        if self.status is LeadStatus.CONVERTED:
            raise LeadStateError("A converted lead cannot be disqualified.")
        cleaned = " ".join(reason.split())
        if not cleaned:
            raise DomainValidationError("A disqualification reason is required.")
        self.status = LeadStatus.UNQUALIFIED
        self.disqualify_reason = cleaned[:MAX_REASON_LENGTH]
        self.version += 1
        return LeadDisqualified(lead_id=self.id)

    def convert(self, customer_id: UUID) -> LeadConverted:
        if self.status is not LeadStatus.QUALIFIED:
            raise LeadStateError("Only a qualified lead can be converted.")
        self.status = LeadStatus.CONVERTED
        self.converted_customer_id = customer_id
        self.version += 1
        return LeadConverted(lead_id=self.id, customer_id=customer_id)
