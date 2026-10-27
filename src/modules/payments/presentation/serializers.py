from __future__ import annotations

from decimal import Decimal
from typing import Any

from rest_framework import serializers

from modules.payments.domain.invoice import Invoice


class InvoiceCreateSerializer(serializers.Serializer[dict[str, Any]]):
    amount = serializers.DecimalField(max_digits=16, decimal_places=2, min_value=Decimal("0.01"))
    currency = serializers.CharField(max_length=3, default="USD")
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
