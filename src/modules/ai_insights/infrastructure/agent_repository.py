from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from django.db.models import F, QuerySet

from modules.ai_insights.domain.agent import ActionStatus, AgentAction, AgentRun, Tool
from modules.ai_insights.domain.insight import InsightStatus
from modules.ai_insights.infrastructure.models import AgentRunRecord
from shared.domain.errors import ConflictError


def _action_to_json(a: AgentAction) -> dict[str, Any]:
    return {
        "index": a.index,
        "tool": a.tool.value,
        "subject_ref": a.subject_ref,
        "args": a.args,
        "reason": a.reason,
        "status": a.status.value,
        "result_ref": a.result_ref,
        "error_code": a.error_code,
        "decided_at": a.decided_at.isoformat() if a.decided_at else None,
    }


def _action_from_json(d: dict[str, Any]) -> AgentAction:
    return AgentAction(
        index=d["index"],
        tool=Tool(d["tool"]),
        subject_ref=d["subject_ref"],
        args=d.get("args", {}),
        reason=d.get("reason", ""),
        status=ActionStatus(d["status"]),
        result_ref=d.get("result_ref"),
        error_code=d.get("error_code"),
        decided_at=datetime.fromisoformat(d["decided_at"]) if d.get("decided_at") else None,
    )


def _to_domain(row: AgentRunRecord) -> AgentRun:
    return AgentRun(
        id=row.id,
        requested_by=row.requested_by,
        request=row.request,
        payload=row.payload,
        source_hash=row.source_hash,
        status=InsightStatus(row.status),
        created_at=row.created_at,
        actions=[_action_from_json(a) for a in row.actions],
        ai_job_id=row.ai_job_id,
        summary=row.summary,
        provider=row.provider,
        model=row.model,
        is_mock=row.is_mock,
        error_code=row.error_code,
        finished_at=row.finished_at,
        version=row.version,
    )


class DjangoAgentRunRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def _qs(self) -> QuerySet[AgentRunRecord]:
        return AgentRunRecord.objects.using(self.using)

    def add(self, run: AgentRun) -> None:
        self._qs().create(
            id=run.id,
            requested_by=run.requested_by,
            request=run.request,
            payload=run.payload,
            source_hash=run.source_hash,
            created_at=run.created_at,
            **self._fields(run),
        )

    def get(self, run_id: UUID) -> AgentRun | None:
        row = self._qs().filter(id=run_id).first()
        return None if row is None else _to_domain(row)

    def save(self, run: AgentRun, expected_version: int) -> None:
        updated = (
            self._qs()
            .filter(id=run.id, version=expected_version)
            .update(version=F("version") + 1, **self._fields(run))
        )
        if updated != 1:
            raise ConflictError("The assistant run was modified concurrently; retry.")
        run.version = expected_version + 1

    def _fields(self, run: AgentRun) -> dict[str, Any]:
        return {
            "status": run.status.value,
            "actions": [_action_to_json(a) for a in run.actions],
            "summary": run.summary,
            "ai_job_id": run.ai_job_id,
            "provider": run.provider,
            "model": run.model,
            "is_mock": run.is_mock,
            "error_code": run.error_code,
            "finished_at": run.finished_at,
        }
