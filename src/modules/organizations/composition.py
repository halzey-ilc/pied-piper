"""Public entry points of the organizations module for other modules' presentation layers."""

from __future__ import annotations

import secrets
from typing import Any
from uuid import UUID

from modules.access_control.domain.rbac import (
    AccessPolicy,
    Permission,
    Principal,
    Role,
    product_role,
)
from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.organizations.application.service import OrganizationService
from modules.organizations.infrastructure.models import Membership
from modules.organizations.infrastructure.store import DjangoOrganizationStore
from platform_core.application.context import require_tenant
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def _temporary_password() -> str:
    # 12+ URL-safe characters: shown once, must be changed at first sign-in.
    return secrets.token_urlsafe(12)


def organization_service() -> OrganizationService:
    # Staff and settings live on the control plane; the audit trail in the company's database.
    return OrganizationService(
        store=DjangoOrganizationStore(),
        audit=DjangoAuditLog(require_tenant().db_alias),
        uow=DjangoUnitOfWork("default"),
        passwords=_temporary_password,
    )


def list_memberships_for_user(user_id: UUID) -> list[dict[str, Any]]:
    """Active memberships for ``GET /me/`` (one per person since P0; legacy data may hold more)."""
    rows = (
        Membership.objects.using("default")
        .filter(user_id=user_id, is_active=True, organization__is_active=True)
        .select_related("organization", "department")
        .order_by("organization__slug")
    )
    policy = AccessPolicy()
    out = []
    for m in rows:
        try:
            role = Role(m.role)
        except ValueError:
            continue
        principal = Principal(user_id=user_id, tenant_id=m.organization_id, role=role)
        org = m.organization
        out.append(
            {
                "tenant": org.slug,
                "role": m.role,
                "product_role": product_role(role),
                "company": {
                    "id": str(org.id),
                    "name": org.name,
                    "country": org.country,
                    "currency": org.currency,
                    "timezone": org.timezone,
                },
                "department": (
                    {"id": str(m.department.id), "name": m.department.name}
                    if m.department
                    else None
                ),
                "permissions": sorted(
                    p.value for p in Permission if policy.scope_for(principal, p) is not None
                ),
            }
        )
    return out


def company_settings(tenant_id: UUID) -> dict[str, str]:
    """Currency and timezone of a company, for other modules (defaults, day boundaries)."""
    from modules.organizations.infrastructure.models import Organization

    row = (
        Organization.objects.using("default")
        .filter(id=tenant_id)
        .values("currency", "timezone", "country")
        .first()
    )
    if row is None:
        return {"currency": "KGS", "timezone": "Asia/Bishkek", "country": "KG"}
    return {"currency": row["currency"], "timezone": row["timezone"], "country": row["country"]}


def list_active_members(tenant_id: UUID) -> list[tuple[UUID, str]]:
    """(user id, role) of every active member of an active organization."""
    rows = Membership.objects.using("default").filter(
        organization_id=tenant_id,
        is_active=True,
        organization__is_active=True,
        user__is_active=True,
    )
    return [(row.user_id, row.role) for row in rows.only("user_id", "role")]
