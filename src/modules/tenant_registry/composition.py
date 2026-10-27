from __future__ import annotations

from django.conf import settings

from modules.tenant_registry.application.provisioning import ProvisionTenant
from modules.tenant_registry.infrastructure.migrator import DjangoTenantMigrator
from modules.tenant_registry.infrastructure.provisioner import PostgresDatabaseProvisioner
from modules.tenant_registry.infrastructure.repository import DjangoTenantRegistryRepository
from platform_core.infrastructure.tenancy.services import connection_registry


def build_provision_tenant() -> ProvisionTenant:
    return ProvisionTenant(
        DjangoTenantRegistryRepository(settings.DATABASE_CLUSTERS),
        PostgresDatabaseProvisioner(settings.DATABASE_CLUSTERS),
        DjangoTenantMigrator(connection_registry()),
        database_prefix=settings.TENANCY["DATABASE_PREFIX"],
        enabled=settings.TENANCY["PROVISIONING_ENABLED"],
    )
