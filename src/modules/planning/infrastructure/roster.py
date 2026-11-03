"""DepartmentRoster adapter: active members of a department of the active company."""

from __future__ import annotations

from uuid import UUID

from modules.organizations.composition import active_member_departments
from modules.organizations.infrastructure.models import Department


class DjangoDepartmentRoster:
    def __init__(self, tenant_id: UUID) -> None:
        self.tenant_id = tenant_id

    def members_of(self, department_id: UUID | None) -> frozenset[UUID]:
        # Belonging to a department is the only basis: no department, no members.
        if department_id is None:
            return frozenset()
        return frozenset(
            user_id
            for user_id, _, member_department in active_member_departments(self.tenant_id)
            if member_department == department_id
        )

    def active_departments(self) -> frozenset[UUID]:
        return frozenset(
            Department.objects.using("default")
            .filter(organization_id=self.tenant_id, is_active=True)
            .values_list("id", flat=True)
        )
