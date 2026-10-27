"""CustomerLookup adapter: checks a linked customer exists in the active tenant."""

from __future__ import annotations

from uuid import UUID

from modules.customers.infrastructure.models import CustomerRecord


class DjangoCustomerLookup:
    def __init__(self, using: str) -> None:
        self.using = using

    def exists(self, customer_id: UUID) -> bool:
        return CustomerRecord.objects.using(self.using).filter(id=customer_id).exists()
