"""Provision a tenant database: register -> create database -> migrate -> activate.

Every step is idempotent, so a failed job can simply be re-run. Provisioning is
disabled unless explicitly enabled by configuration; nothing is provisioned
automatically.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from modules.tenant_registry.application.ports import (
    DatabaseProvisioner,
    TenantMigrator,
    TenantRegistryRepository,
)
from modules.tenant_registry.domain.placement import ProvisioningStatus, choose_cluster
from platform_core.domain.tenancy import TenantStatus, database_name_for
from shared.domain.errors import ConflictError

logger = logging.getLogger(__name__)


class ProvisioningDisabledError(ConflictError):
    code = "provisioning_disabled"


@dataclass(frozen=True, slots=True)
class ProvisionTenantCommand:
    organization_id: UUID
    slug: str
    requested_by: UUID | None = None


@dataclass(frozen=True, slots=True)
class ProvisioningResult:
    database_name: str
    cluster: str
    schema_version: str
    created_database: bool
    already_active: bool


class ProvisionTenant:
    def __init__(
        self,
        registry: TenantRegistryRepository,
        provisioner: DatabaseProvisioner,
        migrator: TenantMigrator,
        *,
        database_prefix: str,
        enabled: bool,
    ) -> None:
        self._registry = registry
        self._provisioner = provisioner
        self._migrator = migrator
        self._prefix = database_prefix
        self._enabled = enabled

    def execute(self, command: ProvisionTenantCommand) -> ProvisioningResult:
        if not self._enabled:
            raise ProvisioningDisabledError("Tenant provisioning is disabled by configuration.")

        existing = self._registry.get(command.organization_id)
        if existing is not None and existing.status is TenantStatus.ACTIVE:
            return ProvisioningResult(
                database_name=existing.database_name,
                cluster=existing.cluster,
                schema_version=existing.schema_version or "",
                created_database=False,
                already_active=True,
            )

        job_id = self._registry.start_job(command.organization_id, command.requested_by)
        try:
            if existing is None:
                cluster = choose_cluster(self._registry.cluster_capacity())
                database_name = database_name_for(command.slug, self._prefix)
                locator = self._registry.register(
                    command.organization_id, command.slug, cluster, database_name
                )
            else:
                locator = existing

            self._registry.update_job(job_id, ProvisioningStatus.CREATING_DATABASE)
            created = self._provisioner.create_database(locator.cluster, locator.database_name)

            self._registry.update_job(job_id, ProvisioningStatus.MIGRATING)
            version = self._migrator.migrate(locator)

            self._registry.mark_active(command.organization_id, version)
            self._registry.update_job(job_id, ProvisioningStatus.SUCCEEDED)
        except Exception as exc:
            self._registry.mark_failed(command.organization_id)
            self._registry.update_job(job_id, ProvisioningStatus.FAILED, error=type(exc).__name__)
            logger.exception("tenant_provisioning_failed", extra={"job_id": str(job_id)})
            raise
        logger.info(
            "tenant_provisioned",
            extra={"job_id": str(job_id), "cluster": locator.cluster, "created": created},
        )
        return ProvisioningResult(
            database_name=locator.database_name,
            cluster=locator.cluster,
            schema_version=version,
            created_database=created,
            already_active=False,
        )
