"""Names of the customer behind a record: what must be masked in text sent to the AI Platform.

System reads inside the active tenant: they only tell the masking what to hide and never leave
the CRM, so they do not depend on the requesting user's rights.
"""

from __future__ import annotations

from uuid import UUID

from modules.customers.infrastructure.models import CustomerRecord
from modules.sales_pipeline.infrastructure.models import DealRecord
from platform_core.application.context import require_tenant


def customer_names(customer_id: UUID | None) -> tuple[str, ...]:
    if customer_id is None:
        return ()
    alias = require_tenant().db_alias
    name = CustomerRecord.objects.using(alias).filter(id=customer_id).values_list("name", flat=True)
    return tuple(name)


def deal_customer_names(deal_id: UUID) -> tuple[str, ...]:
    alias = require_tenant().db_alias
    customer = (
        DealRecord.objects.using(alias).filter(id=deal_id).values_list("customer_id", flat=True)
    ).first()
    return customer_names(customer)
