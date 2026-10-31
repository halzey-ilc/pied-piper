"""Wires the invoice use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.payments.application.payment_service import PaymentService
from modules.payments.application.service import InvoiceService
from modules.payments.infrastructure.lookups import (
    DjangoCustomerLookup,
    DjangoDealLookup,
    DjangoDealValueLookup,
)
from modules.payments.infrastructure.payment_repository import DjangoPaymentRepository
from modules.payments.infrastructure.repository import DjangoInvoiceRepository
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def invoice_service() -> InvoiceService:
    alias = require_tenant().db_alias
    return InvoiceService(
        repository=DjangoInvoiceRepository(alias),
        customers=DjangoCustomerLookup(alias),
        deals=DjangoDealLookup(alias),
        uow=DjangoUnitOfWork(alias),
        outbox=DjangoEventOutbox(alias),
        audit=DjangoAuditLog(alias),
    )


def payment_service() -> PaymentService:
    alias = require_tenant().db_alias
    return PaymentService(
        payments=DjangoPaymentRepository(alias),
        deals=DjangoDealLookup(alias),
        customers=DjangoCustomerLookup(alias),
        deal_values=DjangoDealValueLookup(alias),
        uow=DjangoUnitOfWork(alias),
        outbox=DjangoEventOutbox(alias),
        audit=DjangoAuditLog(alias),
    )
