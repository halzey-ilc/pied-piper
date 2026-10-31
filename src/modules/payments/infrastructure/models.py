from __future__ import annotations

from django.db import models
from django.db.models import Q

from modules.payments.domain.invoice import InvoiceStatus
from modules.payments.domain.payment import PaymentKind


class InvoiceRecord(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    number = models.CharField(max_length=32, unique=True)
    total_amount = models.DecimalField(max_digits=16, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    currency = models.CharField(max_length=3)
    status = models.CharField(
        max_length=8,
        choices=[(s.value, s.name.title()) for s in InvoiceStatus],
        default=InvoiceStatus.DRAFT.value,
    )
    customer_id = models.UUIDField(null=True, blank=True)
    deal_id = models.UUIDField(null=True, blank=True)
    owner_user_id = models.UUIDField()
    team_id = models.UUIDField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payments_invoice"
        indexes = [
            models.Index(fields=["status"], name="invoice_status_idx"),
            models.Index(fields=["customer_id"], name="invoice_customer_idx"),
            models.Index(fields=["deal_id"], name="invoice_deal_idx"),
            models.Index(fields=["owner_user_id"], name="invoice_owner_idx"),
            models.Index(fields=["team_id"], name="invoice_team_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.number} ({self.status})"


class PaymentRecord(models.Model):
    """One received or refunded payment. Stored individually to support prepayments,
    instalments, full payments and refunds; never mutated into a running balance."""

    id = models.UUIDField(primary_key=True, editable=False)
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    currency = models.CharField(max_length=3)
    kind = models.CharField(max_length=10, choices=[(k.value, k.name.title()) for k in PaymentKind])
    paid_on = models.DateField()
    seller_user_id = models.UUIDField()
    deal_id = models.UUIDField(null=True, blank=True)
    customer_id = models.UUIDField(null=True, blank=True)
    is_refund = models.BooleanField(default=False)
    reference = models.CharField(max_length=100, null=True, blank=True)  # noqa: DJ001
    owner_user_id = models.UUIDField()
    team_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "payments_payment"
        indexes = [
            models.Index(fields=["deal_id", "paid_on"], name="payment_deal_idx"),
            models.Index(fields=["customer_id"], name="payment_customer_idx"),
            models.Index(fields=["seller_user_id"], name="payment_seller_idx"),
            models.Index(fields=["owner_user_id"], name="payment_owner_idx"),
            models.Index(fields=["team_id"], name="payment_team_idx"),
            models.Index(fields=["paid_on"], name="payment_paid_on_idx"),
        ]
        constraints = [
            # Idempotency: a reference, when present, is unique per tenant.
            models.UniqueConstraint(
                fields=["reference"],
                condition=Q(reference__isnull=False),
                name="payment_reference_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.amount} {self.currency} ({self.kind})"
