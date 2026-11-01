"""Adapters for task subjects and assignees: what exists, what the caller sees, who is a member."""

from __future__ import annotations

from uuid import UUID

from modules.access_control.domain.rbac import Principal, Role, product_role
from modules.activities.application.ports import MemberInfo
from modules.activities.domain.activity import SubjectType
from modules.customers.infrastructure.models import CustomerRecord
from modules.leads.infrastructure.models import LeadRecord
from modules.organizations.composition import active_member_departments
from modules.sales_pipeline.composition import deal_service
from modules.sales_pipeline.infrastructure.models import DealRecord
from shared.domain.errors import NotFoundError

# Stored roles of a head of sales (РОП), including the legacy "manager" value.
_HEAD_OF_SALES_ROLES = frozenset(r.value for r in Role if product_role(r) == "head_of_sales")


class DjangoSubjectChecker:
    def __init__(self, using: str) -> None:
        self.using = using

    def exists(self, subject_type: SubjectType, subject_id: UUID) -> bool:
        if subject_type is SubjectType.CUSTOMER:
            return CustomerRecord.objects.using(self.using).filter(id=subject_id).exists()
        if subject_type is SubjectType.LEAD:
            return LeadRecord.objects.using(self.using).filter(id=subject_id).exists()
        return DealRecord.objects.using(self.using).filter(id=subject_id).exists()

    def deal_visible_to(self, principal: Principal, deal_id: UUID) -> bool:
        try:
            deal_service().get(principal, deal_id)
        except NotFoundError:
            return False
        return True


class DjangoMemberDirectory:
    def __init__(self, tenant_id: UUID) -> None:
        self.tenant_id = tenant_id
        self._members: list[tuple[UUID, str, UUID | None]] | None = None

    def _all(self) -> list[tuple[UUID, str, UUID | None]]:
        if self._members is None:
            self._members = active_member_departments(self.tenant_id)
        return self._members

    def active_member(self, user_id: UUID) -> MemberInfo | None:
        for member_id, _, department_id in self._all():
            if member_id == user_id:
                return MemberInfo(user_id=member_id, department_id=department_id)
        return None

    def heads_of_sales_count(self) -> int:
        return sum(1 for _, role, _ in self._all() if role in _HEAD_OF_SALES_ROLES)
