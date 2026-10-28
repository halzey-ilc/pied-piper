from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db.models import F, QuerySet

from modules.ai_insights.domain.insight import CallInsight, InsightKind, InsightStatus
from modules.ai_insights.infrastructure.models import CallInsightRecord
from shared.domain.errors import ConflictError


def _to_domain(row: CallInsightRecord) -> CallInsight:
    return CallInsight(
        id=row.id,
        call_id=row.call_id,
        kind=InsightKind(row.kind),
        status=InsightStatus(row.status),
        requested_by=row.requested_by,
        source_hash=row.source_hash,
        created_at=row.created_at,
        ai_job_id=row.ai_job_id,
        result=row.result,
        schema_id=row.schema_id,
        provider=row.provider,
        model=row.model,
        is_mock=row.is_mock,
        error_code=row.error_code,
        finished_at=row.finished_at,
        version=row.version,
    )


class DjangoInsightRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def _qs(self) -> QuerySet[CallInsightRecord]:
        return CallInsightRecord.objects.using(self.using)

    def add(self, insight: CallInsight) -> None:
        self._qs().create(
            id=insight.id,
            call_id=insight.call_id,
            kind=insight.kind.value,
            requested_by=insight.requested_by,
            source_hash=insight.source_hash,
            created_at=insight.created_at,
            **self._fields(insight),
        )

    def get(self, insight_id: UUID) -> CallInsight | None:
        row = self._qs().filter(id=insight_id).first()
        return None if row is None else _to_domain(row)

    def latest_for_call(self, call_id: UUID, kind: InsightKind) -> CallInsight | None:
        row = self._qs().filter(call_id=call_id, kind=kind.value).order_by("-created_at").first()
        return None if row is None else _to_domain(row)

    def find_reusable(
        self, call_id: UUID, kind: InsightKind, source_hash: str
    ) -> CallInsight | None:
        row = (
            self._qs()
            .filter(call_id=call_id, kind=kind.value, source_hash=source_hash)
            .exclude(status=InsightStatus.FAILED.value)
            .order_by("-created_at")
            .first()
        )
        return None if row is None else _to_domain(row)

    def save(self, insight: CallInsight, expected_version: int) -> None:
        updated = (
            self._qs()
            .filter(id=insight.id, version=expected_version)
            .update(version=F("version") + 1, **self._fields(insight))
        )
        if updated != 1:
            raise ConflictError("The AI insight was modified concurrently; retry.")
        insight.version = expected_version + 1

    def _fields(self, insight: CallInsight) -> dict[str, Any]:
        return {
            "status": insight.status.value,
            "ai_job_id": insight.ai_job_id,
            "result": insight.result,
            "schema_id": insight.schema_id,
            "provider": insight.provider,
            "model": insight.model,
            "is_mock": insight.is_mock,
            "error_code": insight.error_code,
            "finished_at": insight.finished_at,
        }
