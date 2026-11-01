"""Adapter for task subjects: whether the caller may see the record a task links to."""

from __future__ import annotations

from uuid import UUID

from modules.access_control.domain.rbac import Principal
from modules.activities.domain.activity import SubjectType
from modules.customers.composition import customer_service
from modules.leads.composition import lead_service
from modules.sales_pipeline.composition import deal_service
from shared.domain.errors import NotFoundError


class DjangoSubjectChecker:
    def __init__(self, using: str) -> None:
        self.using = using

    def visible_to(self, principal: Principal, subject_type: SubjectType, subject_id: UUID) -> bool:
        # The owning module's own read rule (existence + RBAC scope), not a raw lookup.
        try:
            if subject_type is SubjectType.CUSTOMER:
                customer_service().get(principal, subject_id)
            elif subject_type is SubjectType.LEAD:
                lead_service().get(principal, subject_id)
            else:
                deal_service().get(principal, subject_id)
        except NotFoundError:
            return False
        return True
