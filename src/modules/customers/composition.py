"""Wires the customer use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.customers.application.service import CustomerService
from modules.customers.infrastructure.repository import DjangoCustomerRepository
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def customer_service() -> CustomerService:
    alias = require_tenant().db_alias  # fails closed without a tenant context
    return CustomerService(
        repository=DjangoCustomerRepository(alias),
        uow=DjangoUnitOfWork(alias),
        outbox=DjangoEventOutbox(alias),
        audit=DjangoAuditLog(alias),
    )
