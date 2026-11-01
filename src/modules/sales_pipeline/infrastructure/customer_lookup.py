"""CustomerLookup adapter: the customer a deal links to, in the active tenant."""

from __future__ import annotations

from uuid import UUID

from modules.customers.infrastructure.models import CustomerRecord
from modules.sales_pipeline.application.ports import LinkedCustomer


class DjangoCustomerLookup:
    def __init__(self, using: str) -> None:
        self.using = using

    def find(self, customer_id: UUID) -> LinkedCustomer | None:
        row = (
            CustomerRecord.objects.using(self.using)
            .filter(id=customer_id)
            .only("owner_user_id", "team_id", "archived_at")
            .first()
        )
        if row is None:
            return None
        return LinkedCustomer(
            owner_user_id=row.owner_user_id,
            team_id=row.team_id,
            is_archived=row.archived_at is not None,
        )
