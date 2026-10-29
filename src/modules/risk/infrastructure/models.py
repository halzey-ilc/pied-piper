from __future__ import annotations

from django.db import models

from modules.risk.domain.rules import Thresholds

SINGLETON_ID = 1
_DEFAULTS = Thresholds()


class RiskSettingsRecord(models.Model):
    """The company's risk thresholds: one row per tenant database."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=SINGLETON_ID, editable=False)
    window_days = models.PositiveSmallIntegerField(default=_DEFAULTS.window_days)
    amount_multiplier = models.PositiveSmallIntegerField(default=_DEFAULTS.amount_multiplier)
    disqualify_per_day = models.PositiveSmallIntegerField(default=_DEFAULTS.disqualify_per_day)
    lost_per_day = models.PositiveSmallIntegerField(default=_DEFAULTS.lost_per_day)
    voids_per_window = models.PositiveSmallIntegerField(default=_DEFAULTS.voids_per_window)
    updated_by = models.UUIDField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "risk_settings"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(id=SINGLETON_ID), name="risk_settings_singleton"
            ),
        ]

    def __str__(self) -> str:
        return f"risk window {self.window_days}d"
