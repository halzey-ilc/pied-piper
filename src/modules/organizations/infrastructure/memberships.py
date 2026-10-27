"""MembershipProvider adapter (port defined in platform_core.application.ports)."""

from __future__ import annotations

from uuid import UUID

from modules.organizations.infrastructure.models import Membership
from platform_core.application.ports import TenantMembership


class DjangoMembershipProvider:
    def get_membership(self, user_id: UUID, tenant_id: UUID) -> TenantMembership | None:
        row = (
            Membership.objects.using("default")
            .filter(
                user_id=user_id,
                organization_id=tenant_id,
                is_active=True,
                organization__is_active=True,
                user__is_active=True,
            )
            .only("role")
            .first()
        )
        if row is None:
            return None
        return TenantMembership(user_id=user_id, tenant_id=tenant_id, role=row.role)
