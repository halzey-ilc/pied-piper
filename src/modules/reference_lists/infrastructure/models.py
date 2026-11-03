from __future__ import annotations

from django.db import models


class ReferenceItemRecord(models.Model):
    """One item of a company list (meeting reasons) or a company override of a fixed item
    (a lead source label / visibility, keyed by its code)."""

    id = models.UUIDField(primary_key=True, editable=False)
    list_key = models.CharField(max_length=32, db_index=True)
    code = models.CharField(max_length=32, null=True, blank=True)  # noqa: DJ001
    label = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    position = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "reference_list_item"
        constraints = [
            models.UniqueConstraint(
                fields=["list_key", "code"],
                condition=models.Q(code__isnull=False),
                name="reference_item_one_override_per_code",
            )
        ]

    def __str__(self) -> str:
        return f"{self.list_key}: {self.label}"
