from __future__ import annotations

from django.db import models

from modules.operations.domain.rules import Thresholds

SINGLETON_ID = 1
_DEFAULTS = Thresholds()


class OperationsSettingsRecord(models.Model):
    """The company's operations thresholds: one row per tenant database."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=SINGLETON_ID, editable=False)
    stale_lead_days = models.PositiveSmallIntegerField(default=_DEFAULTS.stale_lead_days)
    stalled_deal_days = models.PositiveSmallIntegerField(default=_DEFAULTS.stalled_deal_days)
    updated_by = models.UUIDField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "operations_settings"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(id=SINGLETON_ID), name="operations_settings_singleton"
            ),
        ]

    def __str__(self) -> str:
        return f"leads {self.stale_lead_days}d, deals {self.stalled_deal_days}d"
