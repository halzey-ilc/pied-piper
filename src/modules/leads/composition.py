"""Wires the lead use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.leads.application.service import LeadService
from modules.leads.infrastructure.customer_creation import CustomersCustomerCreation
from modules.leads.infrastructure.repository import DjangoLeadRepository
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def lead_service() -> LeadService:
    alias = require_tenant().db_alias  # fails closed without a tenant context
    return LeadService(
        repository=DjangoLeadRepository(alias),
        uow=DjangoUnitOfWork(alias),
        outbox=DjangoEventOutbox(alias),
        audit=DjangoAuditLog(alias),
        customers=CustomersCustomerCreation(),
    )
