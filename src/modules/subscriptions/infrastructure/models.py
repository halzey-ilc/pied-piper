from __future__ import annotations

from django.db import models

from modules.organizations.infrastructure.models import Organization
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
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "subscriptions_subscription"
        indexes = [models.Index(fields=["status"], name="subscription_status_idx")]

    def __str__(self) -> str:
        return f"{self.organization_id}:{self.plan_code}:{self.status}"
