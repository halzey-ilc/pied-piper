from __future__ import annotations

from dataclasses import asdict
from uuid import UUID

from modules.risk.domain.rules import Thresholds
from modules.risk.infrastructure.models import SINGLETON_ID, RiskSettingsRecord


class DjangoRiskSettingsStore:
    def __init__(self, using: str) -> None:
        self.using = using

    def load(self) -> Thresholds:
        row = RiskSettingsRecord.objects.using(self.using).filter(id=SINGLETON_ID).first()
        if row is None:
            return Thresholds()
        return Thresholds(
            window_days=row.window_days,
            amount_multiplier=row.amount_multiplier,
            disqualify_per_day=row.disqualify_per_day,
            lost_per_day=row.lost_per_day,
            voids_per_window=row.voids_per_window,
        )

    def save(self, thresholds: Thresholds, *, updated_by: UUID) -> None:
        RiskSettingsRecord.objects.using(self.using).update_or_create(
            id=SINGLETON_ID, defaults={**asdict(thresholds), "updated_by": updated_by}
        )
