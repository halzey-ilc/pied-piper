"""MembershipProvider adapter (port defined in platform_core.application.ports)."""

from __future__ import annotations

from uuid import UUID

from modules.access_control.domain.rbac import MemberRef
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
            .only("role", "department_id")
            .first()
        )
        if row is None:
            return None
        # TEAM scope = the member's department: records carry it as their team_id.
        department = row.department_id
        return TenantMembership(
            user_id=user_id,
            tenant_id=tenant_id,
            role=row.role,
            team_ids=frozenset({department}) if department else frozenset(),
            department_id=department,
        )


class DjangoMemberDirectory:
    """Active members of one company with their departments (who may receive work)."""

    def __init__(self, tenant_id: UUID) -> None:
        self.tenant_id = tenant_id
        self._members: dict[UUID, MemberRef] | None = None

    def active_member(self, user_id: UUID) -> MemberRef | None:
        if self._members is None:
            rows = Membership.objects.using("default").filter(
                organization_id=self.tenant_id,
                is_active=True,
                organization__is_active=True,
                user__is_active=True,
            )
            self._members = {
                row.user_id: MemberRef(user_id=row.user_id, department_id=row.department_id)
                for row in rows.only("user_id", "department_id")
            }
        return self._members.get(user_id)
