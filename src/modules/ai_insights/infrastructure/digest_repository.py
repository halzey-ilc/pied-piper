from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from django.db.models import F, QuerySet

from modules.ai_insights.domain.digest import DigestTrigger, OperationsDigest
from modules.ai_insights.domain.insight import InsightStatus
from modules.ai_insights.infrastructure.models import OperationsDigestRecord
from shared.domain.errors import ConflictError


def _to_domain(row: OperationsDigestRecord) -> OperationsDigest:
    return OperationsDigest(
        id=row.id,
        requested_by=row.requested_by,
        trigger=DigestTrigger(row.trigger),
        digest_date=row.digest_date,
        payload=row.payload,
        payload_hash=row.payload_hash,
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


class DjangoDigestRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def _qs(self) -> QuerySet[OperationsDigestRecord]:
        return OperationsDigestRecord.objects.using(self.using)

    def add(self, digest: OperationsDigest) -> None:
        self._qs().create(
            id=digest.id,
            requested_by=digest.requested_by,
            trigger=digest.trigger.value,
            digest_date=digest.digest_date,
            payload=digest.payload,
            payload_hash=digest.payload_hash,
            created_at=digest.created_at,
            **self._fields(digest),
        )

    def get(self, digest_id: UUID) -> OperationsDigest | None:
        row = self._qs().filter(id=digest_id).first()
        return None if row is None else _to_domain(row)

    def latest_for_user(self, user_id: UUID) -> OperationsDigest | None:
        row = self._qs().filter(requested_by=user_id).order_by("-created_at").first()
        return None if row is None else _to_domain(row)

    def find_reusable(self, user_id: UUID, payload_hash: str) -> OperationsDigest | None:
        row = (
            self._qs()
            .filter(requested_by=user_id, payload_hash=payload_hash)
            .exclude(status=InsightStatus.FAILED.value)
            .order_by("-created_at")
            .first()
        )
        return None if row is None else _to_domain(row)

    def exists_for_day(self, user_id: UUID, trigger: DigestTrigger, day: date) -> bool:
        return (
            self._qs().filter(requested_by=user_id, trigger=trigger.value, digest_date=day).exists()
        )

    def save(self, digest: OperationsDigest, expected_version: int) -> None:
        updated = (
            self._qs()
            .filter(id=digest.id, version=expected_version)
            .update(version=F("version") + 1, **self._fields(digest))
        )
        if updated != 1:
            raise ConflictError("The operations digest was modified concurrently; retry.")
        digest.version = expected_version + 1

    def _fields(self, digest: OperationsDigest) -> dict[str, Any]:
        return {
            "status": digest.status.value,
            "ai_job_id": digest.ai_job_id,
            "result": digest.result,
            "provider": digest.provider,
            "model": digest.model,
            "is_mock": digest.is_mock,
            "error_code": digest.error_code,
            "finished_at": digest.finished_at,
        }
