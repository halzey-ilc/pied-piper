"""Tenant resolution use case."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from platform_core.application.context import TenantContext
from platform_core.application.ports import (
    MembershipProvider,
    TenantConnectionRegistry,
    TenantDirectory,
    TenantMembership,
)
from platform_core.domain.tenancy import (
    TenantDatabaseLocator,
    TenantNotFoundError,
    validate_tenant_slug,
)
from shared.domain.errors import DomainValidationError


@dataclass(frozen=True, slots=True)
class ResolvedTenant:
    context: TenantContext
    membership: TenantMembership | None


class TenantResolver:
    """Turns a tenant slug into a routable context.

    Unknown, non-active and not-a-member cases raise the same error so callers
    cannot enumerate tenants.
    """

    def __init__(
        self,
        directory: TenantDirectory,
        connections: TenantConnectionRegistry,
        memberships: MembershipProvider,
    ) -> None:
        self._directory = directory
        self._connections = connections
        self._memberships = memberships

    def resolve_for_user(self, slug: str | None, user_id: UUID) -> ResolvedTenant:
        locator = self._locate(slug)
        membership = self._memberships.get_membership(user_id, locator.tenant_id)
        if membership is None:
            raise TenantNotFoundError("Tenant not found.")
        return ResolvedTenant(self._context(locator), membership)

    def resolve_for_system(self, slug: str | None) -> ResolvedTenant:
        """For trusted callers only (verified webhooks, workers): no membership check."""
        return ResolvedTenant(self._context(self._locate(slug)), None)

    def _locate(self, slug: str | None) -> TenantDatabaseLocator:
        if not slug:
            raise TenantNotFoundError("Tenant not found.")
        try:
            validate_tenant_slug(slug)
        except DomainValidationError:
            raise TenantNotFoundError("Tenant not found.") from None
        locator = self._directory.get_by_slug(slug)
        if locator is None or not locator.is_routable:
            raise TenantNotFoundError("Tenant not found.")
        return locator

    def _context(self, locator: TenantDatabaseLocator) -> TenantContext:
        alias = self._connections.ensure_alias(locator)
        return TenantContext(tenant_id=locator.tenant_id, slug=locator.slug, db_alias=alias)
