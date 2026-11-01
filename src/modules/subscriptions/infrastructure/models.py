from __future__ import annotations

from django.db import models
from django.db.models import Q

from modules.organizations.infrastructure.models import Organization
from modules.subscriptions.domain.payment import PaymentSource, PaymentStatus
from modules.subscriptions.domain.plan import PlanCode
from modules.subscriptions.domain.subscription import SubscriptionStatus


class SubscriptionRecord(models.Model):
    """One subscription per organization. Control plane (global database)."""

    id = models.UUIDField(primary_key=True, editable=False)
    organization = models.OneToOneField(
        Organization, on_delete=models.CASCADE, related_name="subscription"
    )
    plan_code = models.CharField(
        max_length=16, choices=[(p.value, p.name.title()) for p in PlanCode]
    )
    status = models.CharField(
        max_length=12,
        choices=[(s.value, s.name.title()) for s in SubscriptionStatus],
        default=SubscriptionStatus.ACTIVE.value,
    )
    started_at = models.DateTimeField()
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    # Switched-on modules (Feature values); NULL = no restriction (pre-catalog subscriptions).
    features = models.JSONField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "subscriptions_subscription"
        indexes = [
            models.Index(fields=["status"], name="subscription_status_idx"),
            models.Index(fields=["current_period_end"], name="subscription_period_end_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.organization_id}:{self.plan_code}:{self.status}"


class SubscriptionPaymentRecord(models.Model):
    """A payment for a company's subscription received by the platform (control plane)."""

    id = models.UUIDField(primary_key=True, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="subscription_payments"
    )
    subscription = models.ForeignKey(
        SubscriptionRecord, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3)
    paid_on = models.DateField()
    reference = models.CharField(max_length=100)
    source = models.CharField(
        max_length=10, choices=[(s.value, s.name.title()) for s in PaymentSource]
    )
    status = models.CharField(
        max_length=10, choices=[(s.value, s.name.title()) for s in PaymentStatus]
    )
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=500, blank=True, default="")
    recorded_by = models.UUIDField()
    decided_by = models.UUIDField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "subscriptions_payment"
        constraints = [
            # One external operation is recorded once (cancelled ones included: a reused
            # reference is a mistake to look at, not a new payment).
            models.UniqueConstraint(
                fields=["source", "reference"], name="subscription_payment_reference_unique"
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=0), name="subscription_payment_amount_positive"
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "paid_on"], name="sub_payment_org_paid_idx"),
            models.Index(fields=["status", "paid_on"], name="sub_payment_status_paid_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.organization_id}:{self.amount} {self.currency}:{self.status}"
