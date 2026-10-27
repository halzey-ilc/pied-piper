"""TenantDirectory adapter (port defined in platform_core.application.ports)."""

from __future__ import annotations

from collections.abc import Iterator

from modules.tenant_registry.infrastructure.models import TenantDatabase
from platform_core.domain.tenancy import TenantDatabaseLocator, TenantStatus


def to_locator(row: TenantDatabase) -> TenantDatabaseLocator:
    return TenantDatabaseLocator(
        tenant_id=row.organization_id,
        slug=row.slug,
        cluster=row.cluster,
        database_name=row.database_name,
        status=TenantStatus(row.status),
        schema_version=row.schema_version or None,
    )


class DjangoTenantDirectory:
    def get_by_slug(self, slug: str) -> TenantDatabaseLocator | None:
        row = (
            TenantDatabase.objects.using("default")
            .filter(slug=slug, organization__is_active=True)
            .first()
        )
        return None if row is None else to_locator(row)

    def list_routable(self) -> Iterator[TenantDatabaseLocator]:
        rows = (
            TenantDatabase.objects.using("default")
            .filter(status=TenantStatus.ACTIVE.value, organization__is_active=True)
            .order_by("slug")
        )
        for row in rows.iterator(chunk_size=200):
            yield to_locator(row)
