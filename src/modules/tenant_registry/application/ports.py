from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from modules.tenant_registry.domain.placement import ClusterCapacity, ProvisioningStatus
from platform_core.domain.tenancy import TenantDatabaseLocator


class TenantRegistryRepository(Protocol):
    def get(self, organization_id: UUID) -> TenantDatabaseLocator | None: ...

    def cluster_capacity(self) -> Mapping[str, ClusterCapacity]: ...

    def register(
        self, organization_id: UUID, slug: str, cluster: str, database_name: str
    ) -> TenantDatabaseLocator:
        """Create (or return the existing) registry row in PROVISIONING state."""
        ...

    def mark_active(self, organization_id: UUID, schema_version: str) -> None: ...

    def mark_failed(self, organization_id: UUID) -> None: ...

    def start_job(self, organization_id: UUID, requested_by: UUID | None) -> UUID:
        """Create a provisioning job; ConflictError if one is already in progress."""
        ...

    def update_job(
        self, job_id: UUID, status: ProvisioningStatus, error: str | None = None
    ) -> None: ...


class DatabaseProvisioner(Protocol):
    def create_database(self, cluster: str, database_name: str) -> bool:
        """Create the database if absent. Returns True if it was created now."""
        ...


class TenantMigrator(Protocol):
    def migrate(self, locator: TenantDatabaseLocator) -> str:
        """Apply tenant migrations; return the resulting schema version."""
        ...
