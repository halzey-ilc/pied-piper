"""Ports implemented by infrastructure adapters or by business modules."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from platform_core.domain.tenancy import TenantDatabaseLocator
from shared.domain.events import DomainEvent

# --- Tenancy ----------------------------------------------------------------


class TenantDirectory(Protocol):
    """Read access to the tenant registry (control plane)."""

    def get_by_slug(self, slug: str) -> TenantDatabaseLocator | None: ...

    def list_routable(self) -> Iterable[TenantDatabaseLocator]: ...


@dataclass(frozen=True, slots=True)
class TenantMembership:
    user_id: UUID
    tenant_id: UUID
    role: str
    team_ids: frozenset[UUID] = frozenset()
    department_id: UUID | None = None


class MembershipProvider(Protocol):
    def get_membership(self, user_id: UUID, tenant_id: UUID) -> TenantMembership | None: ...


class TenantAccessGate(Protocol):
    """Commercial access of a tenant (subscription term and modules) for one API path.

    Raises an AccessDeniedError subclass when the path is closed for the tenant.
    """

    def check(self, tenant_id: UUID, path: str) -> None: ...


class ErrorRecorder(Protocol):
    """Keeps a safe record of an unexpected server error for the platform administrator.

    Receives no exception message or request data: only where and what kind of error.
    """

    def record(
        self,
        *,
        component: str,
        view: str,
        method: str,
        route: str,
        exception_type: str,
        request_id: str | None,
        tenant_slug: str | None,
    ) -> None: ...


class TenantConnectionRegistry(Protocol):
    """Registers database aliases for tenants on demand (bounded, never all at once)."""

    def ensure_alias(self, locator: TenantDatabaseLocator) -> str: ...

    def release(self, alias: str) -> None: ...


# --- Messaging --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_id: UUID
    event_type: str
    aggregate_type: str
    aggregate_id: str
    occurred_at: datetime
    payload: dict[str, Any]
    correlation_id: str | None
    tenant_slug: str | None

    def as_message(self) -> dict[str, Any]:
        return {
            "event_id": str(self.event_id),
            "event_type": self.event_type,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "occurred_at": self.occurred_at.isoformat(),
            "payload": self.payload,
            "correlation_id": self.correlation_id,
            "tenant_slug": self.tenant_slug,
        }


class EventOutbox(Protocol):
    """Records events in the same transaction (and database) as the business change."""

    def add(self, event: DomainEvent, *, aggregate_type: str, aggregate_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    envelope: EventEnvelope
    attempts: int


class OutboxStore(Protocol):
    def claim_batch(self, limit: int, now: datetime) -> Sequence[OutboxRecord]:
        """Lock due pending messages (skip rows locked by other relays)."""
        ...

    def mark_published(self, event_id: UUID, now: datetime) -> None: ...

    def schedule_retry(self, event_id: UUID, error: str, available_at: datetime) -> None: ...

    def mark_failed(self, event_id: UUID, error: str) -> None: ...


class MessagePublisher(Protocol):
    def publish(self, envelope: EventEnvelope) -> None: ...


class InboxStore(Protocol):
    def register(
        self,
        *,
        source: str,
        message_id: str,
        consumer: str,
        payload: dict[str, Any],
        correlation_id: str | None,
    ) -> UUID | None:
        """Store the message; return its id, or None if it was already registered."""
        ...

    def mark_processed(self, inbox_id: UUID) -> None: ...
