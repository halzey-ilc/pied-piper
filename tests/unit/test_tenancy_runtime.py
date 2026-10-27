"""Tenant router and connection registry behaviour without touching a database."""

from __future__ import annotations

from uuid import uuid4

import pytest
from django.apps import apps
from django.conf import settings
from django.db import connections

from modules.customers.infrastructure.models import CustomerRecord
from modules.identity.infrastructure.models import User
from platform_core.application.context import TenantContext, tenant_scope
from platform_core.domain.tenancy import (
    CrossTenantAccessError,
    TenantContextMissingError,
    TenantDatabaseLocator,
    TenantStatus,
)
from platform_core.infrastructure.messaging.models import OutboxMessage
from platform_core.infrastructure.tenancy.connections import (
    DjangoTenantConnectionRegistry,
    TenantRoutingError,
)
from platform_core.infrastructure.tenancy.router import TenantRouter

router = TenantRouter()
ACME = TenantContext(tenant_id=uuid4(), slug="acme", db_alias="tenant_acme")


def test_every_installed_app_is_classified() -> None:
    tenancy = settings.TENANCY
    known = (
        tenancy["CONTROL_PLANE_APPS"]
        | tenancy["TENANT_PLANE_APPS"]
        | tenancy["SHARED_APPS"]
        | tenancy["IGNORED_APPS"]
    )
    labels = {config.label for config in apps.get_app_configs()}
    assert labels - known == set(), "classify new apps in settings.TENANCY"
    assert not (tenancy["CONTROL_PLANE_APPS"] & tenancy["TENANT_PLANE_APPS"])


def test_tenant_models_fail_closed_without_context() -> None:
    with pytest.raises(TenantContextMissingError):
        router.db_for_read(CustomerRecord)
    with pytest.raises(TenantContextMissingError):
        router.db_for_write(CustomerRecord)


def test_routing_by_plane() -> None:
    with tenant_scope(ACME):
        assert router.db_for_read(CustomerRecord) == "tenant_acme"
        assert router.db_for_read(User) == "default"  # control plane never follows the tenant
        assert router.db_for_write(OutboxMessage) == "tenant_acme"
    assert router.db_for_write(OutboxMessage) == "default"


def test_object_from_other_tenant_database_is_rejected() -> None:
    foreign = CustomerRecord()
    foreign._state.db = "tenant_globex"
    with tenant_scope(ACME), pytest.raises(CrossTenantAccessError):
        router.db_for_write(CustomerRecord, instance=foreign)


@pytest.mark.parametrize(
    ("db", "app_label", "allowed"),
    [
        ("default", "identity", True),
        ("tenant_acme", "identity", False),
        ("default", "customers", False),
        ("tenant_acme", "customers", True),
        ("default", "messaging", True),
        ("tenant_acme", "audit", True),
    ],
)
def test_migrations_are_split_by_plane(db: str, app_label: str, allowed: bool) -> None:
    assert router.allow_migrate(db, app_label) is allowed


def _locator(slug: str, cluster: str = "primary") -> TenantDatabaseLocator:
    db_name = "crm_tenant_" + slug.replace("-", "_")
    return TenantDatabaseLocator(uuid4(), slug, cluster, db_name, TenantStatus.ACTIVE)


def test_connection_registry_is_bounded_lru_and_lazy() -> None:
    registry = DjangoTenantConnectionRegistry(
        settings.DATABASE_CLUSTERS, alias_prefix="tenant_", max_aliases=2
    )
    try:
        a = registry.ensure_alias(_locator("lru-a"))
        b = registry.ensure_alias(_locator("lru-b"))
        registry.ensure_alias(_locator("lru-a"))  # touch a -> b becomes least recently used
        c = registry.ensure_alias(_locator("lru-c"))
        assert registry.active_aliases == (a, c)
        assert b not in connections.settings
        assert connections.settings[c]["NAME"] == "crm_tenant_lru_c"
        assert connections.settings[c]["CONN_MAX_AGE"] == 0
        assert not any(conn.alias in (a, c) for conn in connections.all(initialized_only=True))
    finally:
        for alias in registry.active_aliases:
            connections.settings.pop(alias, None)


def test_connection_registry_rejects_unknown_cluster_and_bad_names() -> None:
    registry = DjangoTenantConnectionRegistry(
        settings.DATABASE_CLUSTERS, alias_prefix="tenant_", max_aliases=2
    )
    with pytest.raises(TenantRoutingError):
        registry.ensure_alias(_locator("ghost", cluster="nope"))
    with pytest.raises(Exception, match="database name"):
        registry.ensure_alias(
            TenantDatabaseLocator(uuid4(), "evil", "primary", "x; DROP", TenantStatus.ACTIVE)
        )


def test_static_aliases_are_reused() -> None:
    registry = DjangoTenantConnectionRegistry(
        settings.DATABASE_CLUSTERS, alias_prefix="tenant_", max_aliases=1
    )
    assert registry.ensure_alias(_locator("acme")) == "tenant_acme"
    assert registry.active_aliases == ()
