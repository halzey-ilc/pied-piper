from __future__ import annotations

from django.db import models

from modules.payments.domain.invoice import InvoiceStatus


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
