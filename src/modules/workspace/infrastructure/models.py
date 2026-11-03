from __future__ import annotations

from django.db import models

COMPANY_SCOPE = "company"


class WorkspaceLayoutRecord(models.Model):
    """One layout per scope: the company (`scope_key="company"`) or a department (its id)."""

    scope_key = models.CharField(max_length=36, primary_key=True)
    department_id = models.UUIDField(null=True, blank=True)
    blocks = models.JSONField(default=list)  # [{"id": "...", "visible": true}, ...] in order
    updated_by = models.UUIDField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "workspace_layout"

    def __str__(self) -> str:
        return self.scope_key
