from __future__ import annotations

from django.core.management import call_command

from platform_core.domain.tenancy import TenantDatabaseLocator
from platform_core.infrastructure.tenancy.connections import DjangoTenantConnectionRegistry
from platform_core.infrastructure.tenancy.schema import applied_schema_version


class DjangoTenantMigrator:
    """Runs ``migrate`` against one tenant database, then releases its connection."""

    def __init__(self, registry: DjangoTenantConnectionRegistry) -> None:
        self._registry = registry

    def migrate(self, locator: TenantDatabaseLocator) -> str:
        alias = self._registry.ensure_alias(locator)
        try:
            call_command("migrate", database=alias, interactive=False, verbosity=0)
            return applied_schema_version(alias)
        finally:
            self._registry.release(alias)
