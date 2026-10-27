"""Tenancy concepts shared by the control plane and the tenant runtime."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from shared.domain.errors import DomainError, DomainValidationError, NotFoundError

_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,38}[a-z0-9]$")
_DB_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,62}$")


class TenantStatus(StrEnum):
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    FAILED = "failed"


class TenantNotFoundError(NotFoundError):
    """Unknown tenant, inactive tenant or tenant the caller may not access (indistinguishable)."""

    code = "tenant_not_found"


class TenantContextMissingError(DomainError):
    """Tenant-plane data was accessed without an active tenant context (programming error)."""

    code = "tenant_context_missing"


class CrossTenantAccessError(DomainError):
    """An object loaded from one tenant database was used under another tenant context."""

    code = "cross_tenant_access"


def validate_tenant_slug(slug: str) -> str:
    if not _SLUG_RE.match(slug):
        raise DomainValidationError(
            "Tenant slug must be 3-40 chars: lowercase letters, digits and hyphens."
        )
    return slug


def validate_database_name(name: str) -> str:
    if not _DB_NAME_RE.match(name):
        raise DomainValidationError("Invalid tenant database name.")
    return name


def database_name_for(slug: str, prefix: str) -> str:
    """Deterministic database naming: prefix + slug with '-' replaced by '_'."""
    return validate_database_name(prefix + validate_tenant_slug(slug).replace("-", "_"))


@dataclass(frozen=True, slots=True)
class TenantDatabaseLocator:
    """Where a tenant's data lives. Contains no credentials."""

    tenant_id: UUID
    slug: str
    cluster: str
    database_name: str
    status: TenantStatus
    schema_version: str | None = None

    @property
    def is_routable(self) -> bool:
        return self.status is TenantStatus.ACTIVE
