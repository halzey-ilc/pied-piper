from __future__ import annotations

from django.db import models

from modules.leads.domain.lead import LeadSource, LeadStatus


class LeadRecord(models.Model):
    """ORM storage for the Lead aggregate. Lives in each tenant's own database."""

    id = models.UUIDField(primary_key=True, editable=False)
    name = models.CharField(max_length=200)
    email = models.EmailField(max_length=254, null=True, blank=True)  # noqa: DJ001
    phone = models.CharField(max_length=16, null=True, blank=True)  # noqa: DJ001
    source = models.CharField(
        max_length=16, choices=[(s.value, s.name.title()) for s in LeadSource]
    )
    status = models.CharField(
        max_length=16,
        choices=[(s.value, s.name.title()) for s in LeadStatus],
        default=LeadStatus.NEW.value,
    )
    owner_user_id = models.UUIDField()
    team_id = models.UUIDField(null=True, blank=True)
    disqualify_reason = models.CharField(max_length=500, null=True, blank=True)  # noqa: DJ001
    converted_customer_id = models.UUIDField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "leads_lead"
        indexes = [
            models.Index(fields=["status"], name="lead_status_idx"),
            models.Index(fields=["owner_user_id"], name="lead_owner_idx"),
            models.Index(fields=["team_id"], name="lead_team_idx"),
            models.Index(fields=["created_at"], name="lead_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.status})"
