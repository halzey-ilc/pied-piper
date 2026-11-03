from __future__ import annotations

from decimal import Decimal
from typing import Any

from rest_framework import serializers

from modules.payments.application.ports import DealRef, DealSummary, SalesMetrics
from modules.payments.domain.invoice import Invoice
from modules.payments.domain.payment import Payment, PaymentKind, PaymentStatus
from modules.payments.domain.receipt import ReceiptInfo


class InvoiceCreateSerializer(serializers.Serializer[dict[str, Any]]):
    amount = serializers.DecimalField(max_digits=16, decimal_places=2, min_value=Decimal("0.01"))
    currency = serializers.CharField(
        max_length=3, required=False, help_text="Default: the company currency"
    )
    number = serializers.CharField(required=False, max_length=32)
    customer_id = serializers.UUIDField(required=False, allow_null=True)
    deal_id = serializers.UUIDField(required=False, allow_null=True)
    due_date = serializers.DateField(required=False, allow_null=True)


class PaymentSerializer(serializers.Serializer[dict[str, Any]]):
    amount = serializers.DecimalField(max_digits=16, decimal_places=2, min_value=Decimal("0.01"))
    currency = serializers.CharField(max_length=3, default="USD")


class VoidSerializer(serializers.Serializer[dict[str, Any]]):
    reason = serializers.CharField(max_length=500)


class InvoiceReadSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    number = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    total_amount = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    paid_amount = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    outstanding_amount = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    currency = serializers.CharField(read_only=True)
    customer_id = serializers.UUIDField(read_only=True, allow_null=True)
    deal_id = serializers.UUIDField(read_only=True, allow_null=True)
    owner_user_id = serializers.UUIDField(read_only=True)
    due_date = serializers.DateField(read_only=True, allow_null=True)
    issued_at = serializers.DateTimeField(read_only=True, allow_null=True)
    paid_at = serializers.DateTimeField(read_only=True, allow_null=True)
    version = serializers.IntegerField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class InvoiceListSerializer(serializers.Serializer[dict[str, Any]]):
    count = serializers.IntegerField(read_only=True)
    results = InvoiceReadSerializer(many=True, read_only=True)


class PaymentRecordCreateSerializer(serializers.Serializer[dict[str, Any]]):
    amount = serializers.DecimalField(max_digits=16, decimal_places=2, min_value=Decimal("0.01"))
    currency = serializers.CharField(
        max_length=3, required=False, help_text="Default: the company currency"
    )
    kind = serializers.ChoiceField(choices=[k.value for k in PaymentKind])
    paid_on = serializers.DateField()
    deal_id = serializers.UUIDField(required=False, allow_null=True)
    customer_id = serializers.UUIDField(required=False, allow_null=True)
    seller_user_id = serializers.UUIDField(required=False, allow_null=True)
    is_refund = serializers.BooleanField(default=False)
    reference = serializers.CharField(required=False, allow_blank=True, max_length=100)
    close_sale = serializers.BooleanField(
        default=False,
        help_text="Recorded from the system stage «Продажа»: confirming it wins the deal",
    )
    receipt = serializers.FileField(
        required=False,
        help_text=(
            "The cheque or bank receipt: PDF, JPEG, PNG, WEBP or HEIC, at most 10 MB "
            "(multipart/form-data). Required for every payment; optional for a refund."
        ),
    )


class PaymentRejectSerializer(serializers.Serializer[dict[str, Any]]):
    reason = serializers.CharField(max_length=500)


class ReceiptInfoSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    content_type = serializers.CharField(read_only=True)
    size = serializers.IntegerField(read_only=True, help_text="Bytes")
    filename = serializers.CharField(read_only=True)
    sha256 = serializers.CharField(read_only=True)
    uploaded_by_user_id = serializers.UUIDField(read_only=True)
    uploaded_at = serializers.DateTimeField(read_only=True)


class PaymentDealSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    title = serializers.CharField(read_only=True)
    amount = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    currency = serializers.CharField(read_only=True)


class PaymentRecordReadSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    amount = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    currency = serializers.CharField(read_only=True)
    kind = serializers.CharField(read_only=True)
    paid_on = serializers.DateField(read_only=True)
    seller_user_id = serializers.UUIDField(read_only=True)
    owner_user_id = serializers.UUIDField(read_only=True)
    deal_id = serializers.UUIDField(read_only=True, allow_null=True)
    customer_id = serializers.UUIDField(read_only=True, allow_null=True)
    is_refund = serializers.BooleanField(read_only=True)
    reference = serializers.CharField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    status = serializers.ChoiceField(
        choices=[s.value for s in PaymentStatus],
        read_only=True,
        help_text="Only confirmed payments count in sales, KPI, plans and reports",
    )
    close_sale = serializers.BooleanField(read_only=True)
    verified_at = serializers.DateTimeField(read_only=True, allow_null=True)
    verified_by_user_id = serializers.UUIDField(read_only=True, allow_null=True)
    verification_method = serializers.CharField(
        read_only=True, allow_null=True, help_text="auto | manual"
    )
    rejection_reason = serializers.CharField(read_only=True, allow_null=True)
    receipt = ReceiptInfoSerializer(read_only=True, allow_null=True)
    can_verify = serializers.BooleanField(
        read_only=True, help_text="Whether you may confirm or reject it now"
    )
    deal = PaymentDealSerializer(read_only=True, allow_null=True, required=False)


class PaymentRecordListSerializer(serializers.Serializer[dict[str, Any]]):
    count = serializers.IntegerField(read_only=True)
    results = PaymentRecordReadSerializer(many=True, read_only=True)


class SalesMetricsSerializer(serializers.Serializer[dict[str, Any]]):
    sales_count = serializers.IntegerField(
        read_only=True, help_text="Deals whose first payment fell in the period"
    )
    new_clients = serializers.IntegerField(
        read_only=True, help_text="Clients whose first payment ever fell in the period"
    )
    turnover = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    received = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    debt = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    currency = serializers.CharField(read_only=True)
    prepayments_count = serializers.IntegerField(
        read_only=True, help_text="Prepayments received in the period (refunds excluded)"
    )
    prepayments_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)


class DealPaymentSummarySerializer(serializers.Serializer[dict[str, Any]]):
    deal_id = serializers.UUIDField(read_only=True)
    deal_amount = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    currency = serializers.CharField(read_only=True)
    confirmed_received = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        read_only=True,
        help_text="Confirmed payments minus refunds",
    )
    pending_amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, read_only=True, help_text="Waiting for verification"
    )
    refunded = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    debt = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        read_only=True,
        help_text="Deal amount minus confirmed received money (not below zero)",
    )
    payments_count = serializers.IntegerField(read_only=True)
    confirmed_count = serializers.IntegerField(read_only=True)
    pending_count = serializers.IntegerField(read_only=True)
    rejected_count = serializers.IntegerField(read_only=True)
    prepayments_count = serializers.IntegerField(read_only=True)
    other_currencies = serializers.ListField(
        child=serializers.CharField(),
        read_only=True,
        help_text="Payments in these currencies are not added to the totals",
    )


def receipt_to_dict(receipt: ReceiptInfo) -> dict[str, Any]:
    return {
        "id": str(receipt.id),
        "content_type": receipt.content_type,
        "size": receipt.size,
        "filename": receipt.filename,
        "sha256": receipt.sha256,
        "uploaded_by_user_id": str(receipt.uploaded_by_user_id),
        "uploaded_at": receipt.uploaded_at.isoformat(),
    }


def deal_summary_to_dict(summary: DealSummary) -> dict[str, Any]:
    return {
        "deal_id": str(summary.deal_id),
        "deal_amount": str(summary.deal_amount),
        "currency": summary.currency,
        "confirmed_received": str(summary.confirmed_received),
        "pending_amount": str(summary.pending_amount),
        "refunded": str(summary.refunded),
        "debt": str(summary.debt),
        "payments_count": summary.payments_count,
        "confirmed_count": summary.confirmed_count,
        "pending_count": summary.pending_count,
        "rejected_count": summary.rejected_count,
        "prepayments_count": summary.prepayments_count,
        "other_currencies": list(summary.other_currencies),
    }


def payment_to_dict(
    payment: Payment,
    receipt: ReceiptInfo | None = None,
    *,
    can_verify: bool = False,
    deal: DealRef | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(payment.id),
        "amount": str(payment.amount.amount),
        "currency": payment.amount.currency,
        "kind": payment.kind.value,
        "paid_on": payment.paid_on.isoformat(),
        "seller_user_id": str(payment.seller_user_id),
        "owner_user_id": str(payment.owner_user_id),
        "deal_id": str(payment.deal_id) if payment.deal_id else None,
        "customer_id": str(payment.customer_id) if payment.customer_id else None,
        "is_refund": payment.is_refund,
        "reference": payment.reference,
        "created_at": payment.created_at.isoformat(),
        "status": payment.status.value,
        "close_sale": payment.close_sale,
        "verified_at": payment.verified_at.isoformat() if payment.verified_at else None,
        "verified_by_user_id": (
            str(payment.verified_by_user_id) if payment.verified_by_user_id else None
        ),
        "verification_method": (
            payment.verification_method.value if payment.verification_method else None
        ),
        "rejection_reason": payment.rejection_reason,
        "receipt": receipt_to_dict(receipt) if receipt is not None else None,
        "can_verify": can_verify,
    }
    if deal is not None:
        out["deal"] = {
            "id": str(deal.id),
            "title": deal.title,
            "amount": str(deal.amount),
            "currency": deal.currency,
        }
    return out


def metrics_to_dict(metrics: SalesMetrics) -> dict[str, Any]:
    return {
        "sales_count": metrics.sales_count,
        "new_clients": metrics.new_clients,
        "turnover": str(metrics.turnover),
        "received": str(metrics.received),
        "debt": str(metrics.debt),
        "currency": metrics.currency,
        "prepayments_count": metrics.prepayments_count,
        "prepayments_amount": str(metrics.prepayments_amount),
        **(
            {
                "by_seller": [
                    {"seller_user_id": str(seller), "sales_count": sales, "new_clients": clients}
                    for seller, (sales, clients) in sorted(
                        metrics.by_seller.items(), key=lambda item: str(item[0])
                    )
                ]
            }
            if metrics.by_seller is not None
            else {}
        ),
    }


def invoice_to_dict(invoice: Invoice) -> dict[str, Any]:
    return {
        "id": str(invoice.id),
        "number": invoice.number,
        "status": invoice.status.value,
        "total_amount": str(invoice.total.amount),
        "paid_amount": str(invoice.paid.amount),
        "outstanding_amount": str(invoice.outstanding.amount),
        "currency": invoice.total.currency,
        "customer_id": str(invoice.customer_id) if invoice.customer_id else None,
        "deal_id": str(invoice.deal_id) if invoice.deal_id else None,
        "owner_user_id": str(invoice.owner_user_id),
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "issued_at": invoice.issued_at.isoformat() if invoice.issued_at else None,
        "paid_at": invoice.paid_at.isoformat() if invoice.paid_at else None,
        "version": invoice.version,
        "created_at": invoice.created_at.isoformat(),
    }
