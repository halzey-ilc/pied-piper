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
from modules.activities.composition import hand_over_open_tasks
from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.customers.composition import hand_over_customers
from modules.leads.composition import hand_over_open_leads
from modules.organizations.application.service import OrganizationService
from modules.organizations.infrastructure.memberships import DjangoMemberDirectory
from modules.organizations.infrastructure.models import Membership
from modules.organizations.infrastructure.store import DjangoOrganizationStore
from modules.sales_pipeline.composition import hand_over_open_deals
from platform_core.application.context import require_tenant
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def temporary_password() -> str:
    # 12+ URL-safe characters: shown once, must be changed at first sign-in.
    return secrets.token_urlsafe(12)


class _TaskHandover:
    """WorkHandover adapter: open tasks (activities), open deals (sales), leads still being
    worked and active customers move together, in one transaction of the company's database."""

    def __init__(self, tenant_id: UUID) -> None:
        self.tenant_id = tenant_id

    def hand_over(self, from_user: UUID, to_user: UUID, actor: UUID) -> int:
        members = DjangoMemberDirectory(self.tenant_id)
        recipient = members.active_member(to_user)
        if recipient is None:
            # The intended recipient left meanwhile: the deciding director takes the work.
            to_user, recipient = actor, members.active_member(actor)
        # A recipient without a department (the director) keeps the records' own team, so
        # the department's head of sales still sees them.
        team = recipient.department_id if recipient is not None else None
        with DjangoUnitOfWork(require_tenant().db_alias):
            tasks = hand_over_open_tasks(from_user, to_user, team, actor)
            deals = hand_over_open_deals(from_user, to_user, team, actor)
            leads = hand_over_open_leads(from_user, to_user, team, actor)
            customers = hand_over_customers(from_user, to_user, team, actor)
        return tasks + deals + leads + customers


def organization_service() -> OrganizationService:
    # Staff and settings live on the control plane; the audit trail in the company's database.
    tenant = require_tenant()
    return OrganizationService(
        store=DjangoOrganizationStore(),
        audit=DjangoAuditLog(tenant.db_alias),
        uow=DjangoUnitOfWork("default"),
        passwords=temporary_password,
        handover=_TaskHandover(tenant.tenant_id),
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


def active_member_departments(tenant_id: UUID) -> list[tuple[UUID, str, UUID | None]]:
    """(user id, role, department id) of every active member of an active organization."""
    rows = Membership.objects.using("default").filter(
        organization_id=tenant_id,
        is_active=True,
        organization__is_active=True,
        user__is_active=True,
    )
    return [
        (row.user_id, row.role, row.department_id)
        for row in rows.only("user_id", "role", "department_id")
    ]


def member_display_name(tenant_id: UUID, user_id: UUID) -> str | None:
    """Full name (or login) of a user who is or was a member of this organization; else None.

    Scoped to the organization so another company's users are never resolved.
    """
    row = (
        Membership.objects.using("default")
        .filter(organization_id=tenant_id, user_id=user_id)
        .select_related("user")
        .first()
    )
    if row is None:
        return None
    user = row.user
    return " ".join(p for p in (user.first_name, user.last_name) if p) or user.get_username()
