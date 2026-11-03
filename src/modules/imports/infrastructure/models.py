from __future__ import annotations

from django.db import models


class ImportJobRecord(models.Model):
    """One completed import: who, what, how many created and why the rest were skipped."""

    id = models.UUIDField(primary_key=True)
    entity = models.CharField(max_length=16)
    filename = models.CharField(max_length=200)
    created_by = models.UUIDField()
    owner_user_id = models.UUIDField()
    total = models.PositiveIntegerField()
    created = models.PositiveIntegerField()
    duplicates = models.PositiveIntegerField()
    invalid = models.PositiveIntegerField()
    failed = models.PositiveIntegerField()
    errors = models.JSONField(default=list)  # [{"line": 3, "message": "..."}], first 50
    created_at = models.DateTimeField()

    class Meta:
        db_table = "imports_job"
        indexes = [models.Index(fields=["-created_at"], name="imports_job_created_idx")]

    def __str__(self) -> str:
        return f"{self.entity}:{self.filename}"
