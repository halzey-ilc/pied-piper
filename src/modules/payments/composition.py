"""Wires the invoice and payment use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.notifications.composition import notification_service
from modules.notifications.domain.notification import NotificationCategory
from modules.payments.application.payment_service import PaymentService
from modules.payments.application.service import InvoiceService
from modules.payments.domain.payment import Payment
from modules.payments.infrastructure.lookups import (
    DjangoCustomerLookup,
    DjangoDealLookup,
    DjangoDealRefLookup,
    DjangoDealValueLookup,
    DjangoDirectorLookup,
)
from modules.payments.infrastructure.payment_repository import (
    DjangoPaymentRepository,
    DjangoReceiptRepository,
)
from modules.payments.infrastructure.receipt_verifier import ManualReviewVerifier
from modules.payments.infrastructure.repository import DjangoInvoiceRepository
from modules.sales_pipeline.application.sale_closing import SaleClosing
from modules.sales_pipeline.infrastructure.repository import (
    DjangoDealRepository,
    DjangoPipelineRepository,
    DjangoStageChangeRepository,
)
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


class _SellerNotifier:
    """In-app notification to the seller when the head of sales rejects their payment."""

    def payment_rejected(self, payment: Payment, reason: str) -> None:
        notification_service().notify(
            recipient_user_id=payment.seller_user_id,
            category=NotificationCategory.PAYMENT,
            title="Оплата не подтверждена",
            body=f"{payment.amount.amount} {payment.amount.currency}. Причина: {reason}"[:1000],
            source_event_id=payment.id,
            link=f"/leads/{payment.deal_id}" if payment.deal_id else None,
        )


def payment_service() -> PaymentService:
    alias = require_tenant().db_alias
    uow = DjangoUnitOfWork(alias)
    outbox = DjangoEventOutbox(alias)
    audit = DjangoAuditLog(alias)
    return PaymentService(
        payments=DjangoPaymentRepository(alias),
        deals=DjangoDealLookup(alias),
        customers=DjangoCustomerLookup(alias),
        deal_values=DjangoDealValueLookup(alias),
        uow=uow,
        outbox=outbox,
        audit=audit,
        receipts=DjangoReceiptRepository(alias),
        verifier=ManualReviewVerifier(),
        deal_refs=DjangoDealRefLookup(alias),
        sale_closer=SaleClosing(
            deals=DjangoDealRepository(alias),
            pipelines=DjangoPipelineRepository(alias),
            stage_changes=DjangoStageChangeRepository(alias),
            uow=DjangoUnitOfWork(alias),  # nested inside the payment's (not re-entrant)
            outbox=outbox,
            audit=audit,
        ),
        notifier=_SellerNotifier(),
        directors=DjangoDirectorLookup(require_tenant().tenant_id),
    )
