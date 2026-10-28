from __future__ import annotations

import uuid
from typing import Any

from django.db import models


class AuditRecord(models.Model):
    """Append-only at two levels.

    The application layer refuses updates and deletes (the overrides below), and
    a database trigger rejects UPDATE/DELETE for every connection regardless of
    the application (migration ``0002_audit_append_only``), so the guarantee
    survives a bypass of the ORM. See docs/architecture/security-model.md.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    occurred_at = models.DateTimeField()
    actor_user_id = models.UUIDField(null=True, blank=True)
    action = models.CharField(max_length=100)
    resource_type = models.CharField(max_length=100)
    resource_id = models.CharField(max_length=64)
    changed_fields = models.JSONField(default=list)
    request_id = models.CharField(max_length=64, blank=True, default="")
    correlation_id = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "audit_record"
        indexes = [
            models.Index(fields=["resource_type", "resource_id"], name="audit_resource_idx"),
            models.Index(fields=["occurred_at"], name="audit_occurred_idx"),
            models.Index(fields=["actor_user_id"], name="audit_actor_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.action}:{self.resource_id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise PermissionError("Audit records are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise PermissionError("Audit records are append-only.")
