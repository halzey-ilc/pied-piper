from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from modules.access_control.domain.rbac import Principal
from modules.leads.domain.lead import Lead, LeadStatus


@dataclass(frozen=True, slots=True)
class LeadVisibility:
    """Row filter derived from the caller's RBAC scope. ``None`` owner = all leads."""

    owner_user_id: UUID | None = None
    team_ids: frozenset[UUID] = field(default_factory=frozenset)


class LeadRepository(Protocol):
    def add(self, lead: Lead) -> None: ...

    def get(self, lead_id: UUID) -> Lead | None: ...

    def save(self, lead: Lead, expected_version: int) -> None:
        """Optimistic concurrency: ConflictError if the stored version differs."""
        ...

    def list_visible(
        self,
        visibility: LeadVisibility,
        *,
        status: LeadStatus | None,
        limit: int,
        offset: int,
    ) -> list[Lead]: ...

    def count(self, visibility: LeadVisibility, *, status: LeadStatus | None) -> int: ...


@dataclass(frozen=True, slots=True)
class DuplicateCandidate:
    """A possible duplicate of a lead: another lead or an existing customer that
    shares its e-mail or phone (by digits)."""

    kind: str  # "lead" or "customer"
    id: UUID
    name: str
    email: str | None
    phone: str | None
    status: str | None = None  # lead status, when kind == "lead"


class DuplicateFinder(Protocol):
    """Finds leads and customers in the tenant that match a lead's e-mail or phone."""

    def find(
        self, *, exclude_lead_id: UUID, email: str | None, phone_digits: str | None
    ) -> list[DuplicateCandidate]: ...


class CustomerCreation(Protocol):
    """Port to the customers context, used when a lead is converted.

    Implemented by an adapter that calls the customers application service, so
    conversion reuses customer creation (validation, RBAC, outbox) instead of
    duplicating it.
    """

    def create(
        self, principal: Principal, *, name: str, email: str | None, phone: str | None
    ) -> UUID: ...
