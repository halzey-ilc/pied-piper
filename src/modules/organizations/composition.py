"""Public entry points of the organizations module for other modules' presentation layers."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from modules.organizations.infrastructure.models import Membership


def list_memberships_for_user(user_id: UUID) -> list[dict[str, Any]]:
    rows = (
        Membership.objects.using("default")
        .filter(user_id=user_id, is_active=True, organization__is_active=True)
        .select_related("organization")
        .order_by("organization__slug")
    )
    return [{"tenant": m.organization.slug, "role": m.role} for m in rows]


def list_active_members(tenant_id: UUID) -> list[tuple[UUID, str]]:
    """(user id, role) of every active member of an active organization."""
    rows = Membership.objects.using("default").filter(
        organization_id=tenant_id,
        is_active=True,
        organization__is_active=True,
        user__is_active=True,
    )
    return [(row.user_id, row.role) for row in rows.only("user_id", "role")]
