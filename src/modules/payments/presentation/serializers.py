from __future__ import annotations

from decimal import Decimal
from typing import Any

from rest_framework import serializers

from modules.payments.application.ports import SalesMetrics
from modules.payments.domain.invoice import Invoice
from modules.payments.domain.payment import Payment, PaymentKind


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


class PaymentRecordListSerializer(serializers.Serializer[dict[str, Any]]):
    count = serializers.IntegerField(read_only=True)
    results = PaymentRecordReadSerializer(many=True, read_only=True)


class SalesMetricsSerializer(serializers.Serializer[dict[str, Any]]):
    sales_count = serializers.IntegerField(read_only=True)
    turnover = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    received = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    debt = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    currency = serializers.CharField(read_only=True)


def payment_to_dict(payment: Payment) -> dict[str, Any]:
    return {
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
    }


def metrics_to_dict(metrics: SalesMetrics) -> dict[str, Any]:
    return {
        "sales_count": metrics.sales_count,
        "turnover": str(metrics.turnover),
        "received": str(metrics.received),
        "debt": str(metrics.debt),
        "currency": metrics.currency,
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
