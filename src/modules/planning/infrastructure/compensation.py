"""Pay terms persisted in the tenant database."""

from __future__ import annotations

from uuid import UUID

from modules.planning.domain.compensation import Compensation
from modules.planning.infrastructure.models import CompensationRecord


def _to_domain(row: CompensationRecord) -> Compensation:
    return Compensation(
        user_id=row.user_id,
        base_salary=row.base_salary,
        commission_percent=row.commission_percent,
        currency=row.currency,
        updated_at=row.updated_at,
    )


class DjangoCompensationRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def get(self, user_id: UUID) -> Compensation | None:
        row = CompensationRecord.objects.using(self.using).filter(user_id=user_id).first()
        return None if row is None else _to_domain(row)

    def list_all(self) -> list[Compensation]:
        return [_to_domain(row) for row in CompensationRecord.objects.using(self.using).all()]

    def upsert(self, compensation: Compensation) -> None:
        CompensationRecord.objects.using(self.using).update_or_create(
            user_id=compensation.user_id,
            defaults={
                "base_salary": compensation.base_salary,
                "commission_percent": compensation.commission_percent,
                "currency": compensation.currency,
                "updated_at": compensation.updated_at,
            },
        )
