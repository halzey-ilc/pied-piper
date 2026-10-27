"""Tenant-aware database router.

* Control-plane apps always use ``default``.
* Tenant-plane apps use the alias of the active tenant context and FAIL CLOSED
  (raise) when no tenant context is active.
* Shared apps (outbox/inbox, audit) follow the tenant context when present and
  the control plane otherwise.
* Migrations: control-plane apps only on ``default``, tenant-plane apps only on
  tenant aliases, shared apps on both.
"""

from __future__ import annotations

from typing import Any, Literal

from django.conf import settings
from django.db.models import Model

from platform_core.application.context import current_tenant
from platform_core.domain.tenancy import CrossTenantAccessError, TenantContextMissingError

Plane = Literal["control", "tenant", "shared"]


def plane_for(app_label: str) -> Plane:
    tenancy = settings.TENANCY
    if app_label in tenancy["TENANT_PLANE_APPS"]:
        return "tenant"
    if app_label in tenancy["SHARED_APPS"]:
        return "shared"
    return "control"


def is_tenant_alias(alias: str) -> bool:
    return bool(alias.startswith(settings.TENANCY["ALIAS_PREFIX"]))


class TenantRouter:
    def _route(self, model: type[Model], **hints: Any) -> str | None:
        plane = plane_for(model._meta.app_label)
        if plane == "control":
            return "default"
        tenant = current_tenant()
        instance = hints.get("instance")
        instance_db = getattr(getattr(instance, "_state", None), "db", None)
        if plane == "tenant":
            if tenant is None:
                raise TenantContextMissingError(
                    f"{model._meta.label} is tenant data; no tenant context is active."
                )
            if instance_db is not None and instance_db != tenant.db_alias:
                raise CrossTenantAccessError("Object belongs to a different tenant database.")
            return tenant.db_alias
        if instance_db is not None:
            return str(instance_db)
        return tenant.db_alias if tenant is not None else "default"

    def db_for_read(self, model: type[Model], **hints: Any) -> str | None:
        return self._route(model, **hints)

    def db_for_write(self, model: type[Model], **hints: Any) -> str | None:
        return self._route(model, **hints)

    def allow_relation(self, obj1: Model, obj2: Model, **hints: Any) -> bool | None:
        db1, db2 = obj1._state.db, obj2._state.db
        if db1 is not None and db2 is not None:
            return db1 == db2
        return None

    def allow_migrate(
        self, db: str, app_label: str, model_name: str | None = None, **hints: Any
    ) -> bool:
        plane = plane_for(app_label)
        tenant_db = is_tenant_alias(db)
        if plane == "control":
            return not tenant_db
        if plane == "tenant":
            return tenant_db
        return True
