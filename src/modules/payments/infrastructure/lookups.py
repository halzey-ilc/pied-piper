"""Customer/deal existence lookups for the active tenant."""

from __future__ import annotations

from decimal import Decimal
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


class DjangoDealValueLookup:
    def __init__(self, using: str) -> None:
        self.using = using

    def values(self, deal_ids: list[UUID]) -> dict[UUID, Decimal]:
        if not deal_ids:
            return {}
        rows = (
            DealRecord.objects.using(self.using).filter(id__in=deal_ids).values_list("id", "amount")
        )
        return {row[0]: row[1] for row in rows}
