from __future__ import annotations

from uuid import UUID

from modules.operations.domain.rules import Thresholds
from modules.operations.infrastructure.models import SINGLETON_ID, OperationsSettingsRecord


class DjangoSettingsStore:
    def __init__(self, using: str) -> None:
        self.using = using

    def load(self) -> Thresholds:
        row = OperationsSettingsRecord.objects.using(self.using).filter(id=SINGLETON_ID).first()
        if row is None:
            return Thresholds()
        return Thresholds(
            stale_lead_days=row.stale_lead_days, stalled_deal_days=row.stalled_deal_days
        )

    def save(self, thresholds: Thresholds, *, updated_by: UUID) -> None:
        OperationsSettingsRecord.objects.using(self.using).update_or_create(
            id=SINGLETON_ID,
            defaults={
                "stale_lead_days": thresholds.stale_lead_days,
                "stalled_deal_days": thresholds.stalled_deal_days,
                "updated_by": updated_by,
            },
        )
