"""Adapters for task subjects and assignees: what exists, what the caller sees, who is a member."""

from __future__ import annotations

from uuid import UUID

from modules.access_control.domain.rbac import Principal
from modules.activities.domain.activity import SubjectType
from modules.customers.infrastructure.models import CustomerRecord
from modules.leads.infrastructure.models import LeadRecord
from modules.organizations.composition import list_active_members
from modules.sales_pipeline.composition import deal_service
from modules.sales_pipeline.infrastructure.models import DealRecord
from shared.domain.errors import NotFoundError


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

    def is_active_member(self, user_id: UUID) -> bool:
        return any(member_id == user_id for member_id, _ in list_active_members(self.tenant_id))
