from __future__ import annotations

from django.db import models
from django.db.models import Q


class CustomerRecord(models.Model):
    """ORM storage for the Customer aggregate. Lives in each tenant's own database,
    so there is deliberately no tenant_id column: isolation is physical."""

    id = models.UUIDField(primary_key=True, editable=False)
    name = models.CharField(max_length=200)
    # null=True (not just blank) so the partial unique index on email treats
    # "no e-mail" as distinct rows rather than colliding empty strings.
    email = models.EmailField(max_length=254, null=True, blank=True)  # noqa: DJ001
    phone = models.CharField(max_length=16, null=True, blank=True)  # noqa: DJ001
    owner_user_id = models.UUIDField()
    team_id = models.UUIDField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "customers_customer"
        indexes = [
            models.Index(fields=["owner_user_id"], name="customer_owner_idx"),
            models.Index(fields=["team_id"], name="customer_team_idx"),
            models.Index(fields=["created_at"], name="customer_created_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["email"], condition=Q(email__isnull=False), name="customer_email_unique"
            ),
        ]

    def __str__(self) -> str:
        return str(self.id)
