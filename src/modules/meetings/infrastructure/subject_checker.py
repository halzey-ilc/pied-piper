"""SubjectChecker adapter: whether the caller may see the customer/deal a meeting links to."""

from __future__ import annotations

from uuid import UUID

from modules.access_control.domain.rbac import Principal
from modules.customers.composition import customer_service
from modules.meetings.domain.meeting import SubjectType
from modules.sales_pipeline.composition import deal_service
from shared.domain.errors import NotFoundError


class DjangoSubjectChecker:
    def __init__(self, using: str) -> None:
        self.using = using

    def visible_to(self, principal: Principal, subject_type: SubjectType, subject_id: UUID) -> bool:
        try:
            if subject_type is SubjectType.CUSTOMER:
                customer_service().get(principal, subject_id)
            else:
                deal_service().get(principal, subject_id)
        except NotFoundError:
            return False
        return True
