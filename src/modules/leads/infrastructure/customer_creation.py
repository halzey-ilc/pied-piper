"""Adapter implementing the CustomerCreation port via the customers context.

Conversion runs inside the lead service's unit of work on the active tenant
database; calling customer creation here nests in the same transaction, so the
new customer and the converted lead commit together.
"""

from __future__ import annotations

from uuid import UUID

from modules.access_control.domain.rbac import Principal
from modules.customers.application.service import CreateCustomer
from modules.customers.composition import customer_service
from modules.customers.domain.customer import CustomerArchivedError


class CustomersCustomerCreation:
    def create(
        self, principal: Principal, *, name: str, email: str | None, phone: str | None
    ) -> UUID:
        customer = customer_service().create(
            principal, CreateCustomer(name=name, email=email, phone=phone)
        )
        return customer.id

    def existing(self, principal: Principal, customer_id: UUID) -> UUID:
        customer = customer_service().get(principal, customer_id)  # 404 if not visible
        if customer.is_archived:
            raise CustomerArchivedError("The customer is archived; restore it first.")
        return customer.id
