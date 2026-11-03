from __future__ import annotations

from django.db import models


class WebFormKeyRecord(models.Model):
    """A website intake key. Control plane: the public request carries no X-Tenant, so the
    token alone must lead to the company (``tenant_id``) and its department."""

    id = models.UUIDField(primary_key=True, editable=False)
    tenant_id = models.UUIDField()
    department_id = models.UUIDField()
    name = models.CharField(max_length=100)
    # A public form id (it is published in the website's HTML), not a credential.
    token = models.CharField(max_length=64, unique=True)
    created_by = models.UUIDField()
    created_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.UUIDField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    leads_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "web_forms_key"
        indexes = [
            models.Index(fields=["tenant_id", "department_id"], name="web_form_key_dept_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({'revoked' if self.revoked_at else 'active'})"
