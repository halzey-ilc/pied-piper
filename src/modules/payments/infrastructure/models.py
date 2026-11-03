from __future__ import annotations

from django.db import models
from django.db.models import Q

from modules.payments.domain.invoice import InvoiceStatus
from modules.payments.domain.payment import PaymentKind, PaymentStatus, VerificationMethod


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


class CountedPaymentManager(models.Manager["PaymentRecord"]):
    """Payments that count in money, sales, KPI, plans and reports: CONFIRMED only.
    Every aggregate reads ``PaymentRecord.counted``; ``objects`` sees all statuses."""

    def get_queryset(self) -> models.QuerySet[PaymentRecord]:
        return super().get_queryset().filter(status=PaymentStatus.CONFIRMED.value)


class PaymentReceiptRecord(models.Model):
    """The uploaded receipt of a payment (PDF or image, at most 10 MB), kept in the
    company database next to the payment. Never deleted with history."""

    id = models.UUIDField(primary_key=True, editable=False)
    content = models.BinaryField()
    content_type = models.CharField(max_length=40)
    size = models.PositiveIntegerField()
    filename = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    uploaded_by_user_id = models.UUIDField()
    uploaded_at = models.DateTimeField()

    class Meta:
        db_table = "payments_receipt"
        indexes = [models.Index(fields=["sha256"], name="payment_receipt_sha_idx")]

    def __str__(self) -> str:
        return f"{self.filename} ({self.content_type}, {self.size} B)"


class PaymentRecord(models.Model):
    """One received or refunded payment. Stored individually to support prepayments,
    instalments, full payments and refunds; never mutated into a running balance.
    Only ``confirmed`` payments count (see ``counted``)."""

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
    status = models.CharField(
        max_length=20,
        choices=[(s.value, s.name.title()) for s in PaymentStatus],
        default=PaymentStatus.CONFIRMED.value,
    )
    receipt = models.ForeignKey(
        PaymentReceiptRecord, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    close_sale = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by_user_id = models.UUIDField(null=True, blank=True)
    verification_method = models.CharField(  # noqa: DJ001
        max_length=10,
        choices=[(m.value, m.name.title()) for m in VerificationMethod],
        null=True,
        blank=True,
    )
    rejection_reason = models.CharField(max_length=500, null=True, blank=True)  # noqa: DJ001

    objects = models.Manager()
    counted = CountedPaymentManager()

    class Meta:
        db_table = "payments_payment"
        indexes = [
            models.Index(fields=["deal_id", "paid_on"], name="payment_deal_idx"),
            models.Index(fields=["customer_id"], name="payment_customer_idx"),
            models.Index(fields=["seller_user_id"], name="payment_seller_idx"),
            models.Index(fields=["owner_user_id"], name="payment_owner_idx"),
            models.Index(fields=["team_id"], name="payment_team_idx"),
            models.Index(fields=["paid_on"], name="payment_paid_on_idx"),
            models.Index(fields=["status", "paid_on"], name="payment_status_idx"),
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
