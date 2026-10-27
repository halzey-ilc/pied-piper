"""Apply tenant-plane migrations to tenant databases, one database at a time.

    python manage.py migrate_tenants            # all active tenants
    python manage.py migrate_tenants --tenant demo
    python manage.py migrate_tenants --check    # report schema drift only

Only one tenant connection is open at any time; each is closed before the next.
``manage.py migrate`` (without this command) migrates the control plane only.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from modules.tenant_registry.infrastructure.directory import DjangoTenantDirectory
from modules.tenant_registry.infrastructure.migrator import DjangoTenantMigrator
from modules.tenant_registry.infrastructure.repository import DjangoTenantRegistryRepository
from platform_core.infrastructure.tenancy.schema import expected_schema_version
from platform_core.infrastructure.tenancy.services import connection_registry


class Command(BaseCommand):
    help = "Migrate tenant databases sequentially and record their schema versions."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--tenant", help="Only this tenant slug")
        parser.add_argument("--check", action="store_true", help="Report drift, change nothing")

    def handle(self, *args: Any, **options: Any) -> None:
        from django.conf import settings

        directory = DjangoTenantDirectory()
        registry_repo = DjangoTenantRegistryRepository(settings.DATABASE_CLUSTERS)
        migrator = DjangoTenantMigrator(connection_registry())
        expected = expected_schema_version()

        if options["tenant"]:
            locator = directory.get_by_slug(options["tenant"])
            if locator is None or not locator.is_routable:
                raise CommandError("Tenant not found or not active.")
            locators = [locator]
        else:
            locators = list(directory.list_routable())

        drift = 0
        for locator in locators:
            if options["check"]:
                if locator.schema_version != expected:
                    drift += 1
                    self.stdout.write(
                        f"{locator.slug}: recorded={locator.schema_version} expected={expected}"
                    )
                continue
            version = migrator.migrate(locator)
            registry_repo.mark_active(locator.tenant_id, version)
            self.stdout.write(f"{locator.slug}: schema_version={version}")

        if options["check"]:
            self.stdout.write(f"{len(locators)} tenant(s) checked, {drift} with drift")
            if drift:
                raise CommandError("Tenant schema drift detected.")
        else:
            self.stdout.write(self.style.SUCCESS(f"{len(locators)} tenant(s) migrated"))
