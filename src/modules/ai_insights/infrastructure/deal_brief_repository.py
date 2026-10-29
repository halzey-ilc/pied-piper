from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db.models import F, QuerySet

from modules.ai_insights.domain.deal_brief import DealBrief
from modules.ai_insights.domain.insight import InsightStatus
from modules.ai_insights.infrastructure.models import DealBriefRecord
from shared.domain.errors import ConflictError


def _to_domain(row: DealBriefRecord) -> DealBrief:
    return DealBrief(
        id=row.id,
        deal_id=row.deal_id,
        requested_by=row.requested_by,
        payload=row.payload,
        source_hash=row.source_hash,
        status=InsightStatus(row.status),
        created_at=row.created_at,
        ai_job_id=row.ai_job_id,
        result=row.result,
        provider=row.provider,
        model=row.model,
        is_mock=row.is_mock,
        error_code=row.error_code,
        finished_at=row.finished_at,
        version=row.version,
    )


class DjangoDealBriefRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def _qs(self) -> QuerySet[DealBriefRecord]:
        return DealBriefRecord.objects.using(self.using)

    def add(self, brief: DealBrief) -> None:
        self._qs().create(
            id=brief.id,
            deal_id=brief.deal_id,
            requested_by=brief.requested_by,
            source_hash=brief.source_hash,
            created_at=brief.created_at,
            **self._fields(brief),
        )

    def get(self, brief_id: UUID) -> DealBrief | None:
        row = self._qs().filter(id=brief_id).first()
        return None if row is None else _to_domain(row)

    def latest_for(self, deal_id: UUID, user_id: UUID) -> DealBrief | None:
        row = (
            self._qs().filter(deal_id=deal_id, requested_by=user_id).order_by("-created_at").first()
        )
        return None if row is None else _to_domain(row)

    def find_reusable(self, deal_id: UUID, user_id: UUID, source_hash: str) -> DealBrief | None:
        row = (
            self._qs()
            .filter(deal_id=deal_id, requested_by=user_id, source_hash=source_hash)
            .exclude(status=InsightStatus.FAILED.value)
            .order_by("-created_at")
            .first()
        )
        return None if row is None else _to_domain(row)

    def save(self, brief: DealBrief, expected_version: int) -> None:
        updated = (
            self._qs()
            .filter(id=brief.id, version=expected_version)
            .update(version=F("version") + 1, **self._fields(brief))
        )
        if updated != 1:
            raise ConflictError("The deal brief was modified concurrently; retry.")
        brief.version = expected_version + 1

    def _fields(self, brief: DealBrief) -> dict[str, Any]:
        return {
            "payload": brief.payload,
            "status": brief.status.value,
            "ai_job_id": brief.ai_job_id,
            "result": brief.result,
            "provider": brief.provider,
            "model": brief.model,
            "is_mock": brief.is_mock,
            "error_code": brief.error_code,
            "finished_at": brief.finished_at,
        }
