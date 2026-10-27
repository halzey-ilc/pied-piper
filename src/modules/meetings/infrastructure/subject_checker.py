"""SubjectChecker adapter: verifies the customer/deal a meeting links to exists."""

from __future__ import annotations

from uuid import UUID

from modules.customers.infrastructure.models import CustomerRecord
from modules.meetings.domain.meeting import SubjectType
from modules.sales_pipeline.infrastructure.models import DealRecord


class DjangoSubjectChecker:
    def __init__(self, using: str) -> None:
        self.using = using

    def exists(self, subject_type: SubjectType, subject_id: UUID) -> bool:
        if subject_type is SubjectType.CUSTOMER:
            return CustomerRecord.objects.using(self.using).filter(id=subject_id).exists()
        return DealRecord.objects.using(self.using).filter(id=subject_id).exists()
