from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db.models import F, QuerySet

from modules.ai_insights.domain.insight import InsightStatus
from modules.ai_insights.domain.reply import ReplySuggestion
from modules.ai_insights.infrastructure.models import ReplySuggestionRecord
from shared.domain.errors import ConflictError


def _to_domain(row: ReplySuggestionRecord) -> ReplySuggestion:
    return ReplySuggestion(
        id=row.id,
        conversation_id=row.conversation_id,
        requested_by=row.requested_by,
        language=row.language,
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


class DjangoReplyRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def _qs(self) -> QuerySet[ReplySuggestionRecord]:
        return ReplySuggestionRecord.objects.using(self.using)

    def add(self, suggestion: ReplySuggestion) -> None:
        self._qs().create(
            id=suggestion.id,
            conversation_id=suggestion.conversation_id,
            requested_by=suggestion.requested_by,
            language=suggestion.language,
            source_hash=suggestion.source_hash,
            created_at=suggestion.created_at,
            **self._fields(suggestion),
        )

    def get(self, suggestion_id: UUID) -> ReplySuggestion | None:
        row = self._qs().filter(id=suggestion_id).first()
        return None if row is None else _to_domain(row)

    def latest_for_conversation(self, conversation_id: UUID) -> ReplySuggestion | None:
        row = self._qs().filter(conversation_id=conversation_id).order_by("-created_at").first()
        return None if row is None else _to_domain(row)

    def find_reusable(self, conversation_id: UUID, source_hash: str) -> ReplySuggestion | None:
        row = (
            self._qs()
            .filter(conversation_id=conversation_id, source_hash=source_hash)
            .exclude(status=InsightStatus.FAILED.value)
            .order_by("-created_at")
            .first()
        )
        return None if row is None else _to_domain(row)

    def save(self, suggestion: ReplySuggestion, expected_version: int) -> None:
        updated = (
            self._qs()
            .filter(id=suggestion.id, version=expected_version)
            .update(version=F("version") + 1, **self._fields(suggestion))
        )
        if updated != 1:
            raise ConflictError("The reply suggestion was modified concurrently; retry.")
        suggestion.version = expected_version + 1

    def _fields(self, suggestion: ReplySuggestion) -> dict[str, Any]:
        return {
            "status": suggestion.status.value,
            "ai_job_id": suggestion.ai_job_id,
            "result": suggestion.result,
            "provider": suggestion.provider,
            "model": suggestion.model,
            "is_mock": suggestion.is_mock,
            "error_code": suggestion.error_code,
            "finished_at": suggestion.finished_at,
        }
