"""Customer aggregate. Pure Python: no Django, no ORM."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import UUID, uuid4

from shared.domain.errors import DomainValidationError
from shared.domain.events import DomainEvent
from shared.domain.value_objects import EmailAddress, PhoneNumber

MAX_NAME_LENGTH = 200


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise DomainValidationError("Customer name must not be empty.")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise DomainValidationError(f"Customer name must be at most {MAX_NAME_LENGTH} characters.")
    return cleaned


@dataclass(frozen=True, kw_only=True)
class CustomerCreated(DomainEvent):
    event_type: ClassVar[str] = "customers.customer_created"
    customer_id: UUID
    owner_user_id: UUID

    def payload(self) -> dict[str, Any]:
        return {"customer_id": str(self.customer_id), "owner_user_id": str(self.owner_user_id)}


@dataclass(frozen=True, kw_only=True)
class CustomerUpdated(DomainEvent):
    event_type: ClassVar[str] = "customers.customer_updated"
    customer_id: UUID
    changed_fields: tuple[str, ...]

    def payload(self) -> dict[str, Any]:
        return {"customer_id": str(self.customer_id), "changed_fields": list(self.changed_fields)}


@dataclass(slots=True)
class Customer:
    id: UUID
    name: str
    owner_user_id: UUID
    email: EmailAddress | None = None
    phone: PhoneNumber | None = None
    team_id: UUID | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    version: int = 1

    @classmethod
    def register(
        cls,
        *,
        name: str,
        owner_user_id: UUID,
        email: str | None = None,
        phone: str | None = None,
        team_id: UUID | None = None,
    ) -> tuple[Customer, CustomerCreated]:
        customer = cls(
            id=uuid4(),
            name=_clean_name(name),
            owner_user_id=owner_user_id,
            email=EmailAddress(email) if email else None,
            phone=PhoneNumber(phone) if phone else None,
            team_id=team_id,
        )
        return customer, CustomerCreated(customer_id=customer.id, owner_user_id=owner_user_id)

    def update_details(
        self,
        *,
        name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
    ) -> CustomerUpdated | None:
        changed: list[str] = []
        if name is not None:
            new_name = _clean_name(name)
            if new_name != self.name:
                self.name = new_name
                changed.append("name")
        if email is not None:
            new_email = EmailAddress(email) if email else None
            if new_email != self.email:
                self.email = new_email
                changed.append("email")
        if phone is not None:
            new_phone = PhoneNumber(phone) if phone else None
            if new_phone != self.phone:
                self.phone = new_phone
                changed.append("phone")
        if not changed:
            return None
        self.version += 1
        return CustomerUpdated(customer_id=self.id, changed_fields=tuple(changed))
