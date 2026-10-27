"""Customer/deal existence lookups for the active tenant."""

from __future__ import annotations

from uuid import UUID

from modules.customers.infrastructure.models import CustomerRecord
from modules.sales_pipeline.infrastructure.models import DealRecord


class DjangoCustomerLookup:
    def __init__(self, using: str) -> None:
        self.using = using

    def exists(self, customer_id: UUID) -> bool:
        return CustomerRecord.objects.using(self.using).filter(id=customer_id).exists()


class DjangoDealLookup:
    def __init__(self, using: str) -> None:
        self.using = using

    def exists(self, deal_id: UUID) -> bool:
        return DealRecord.objects.using(self.using).filter(id=deal_id).exists()
