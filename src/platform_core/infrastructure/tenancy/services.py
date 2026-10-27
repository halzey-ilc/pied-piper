"""Composition of the tenancy runtime (process-wide singletons)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache

from django.conf import settings
from django.utils.module_loading import import_string

from platform_core.application.context import TenantContext, tenant_scope
from platform_core.application.ports import MembershipProvider, TenantDirectory
from platform_core.application.tenancy import TenantResolver
from platform_core.infrastructure.tenancy.connections import DjangoTenantConnectionRegistry


@cache
def connection_registry() -> DjangoTenantConnectionRegistry:
    return DjangoTenantConnectionRegistry(
        settings.DATABASE_CLUSTERS,
        alias_prefix=settings.TENANCY["ALIAS_PREFIX"],
        max_aliases=settings.TENANCY["MAX_ACTIVE_ALIASES"],
    )


@cache
def tenant_directory() -> TenantDirectory:
    directory: TenantDirectory = import_string(settings.TENANCY["DIRECTORY"])()
    return directory


@cache
def membership_provider() -> MembershipProvider:
    provider: MembershipProvider = import_string(settings.TENANCY["MEMBERSHIPS"])()
    return provider


def tenant_resolver() -> TenantResolver:
    return TenantResolver(tenant_directory(), connection_registry(), membership_provider())


@contextmanager
def system_tenant_scope(slug: str) -> Iterator[TenantContext]:
    """Activate a tenant for trusted background work and close its connection afterwards."""
    context = tenant_resolver().resolve_for_system(slug).context
    try:
        with tenant_scope(context):
            yield context
    finally:
        connection_registry().release(context.db_alias)
