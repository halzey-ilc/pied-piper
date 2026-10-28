from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from modules.access_control.domain.rbac import Principal
from modules.ai_insights.domain.insight import CallInsight, InsightKind


@dataclass(frozen=True, slots=True)
class CallSnapshot:
    """What this module needs to know about a call."""

    id: UUID
    owner_user_id: UUID
    team_id: UUID | None
    text: str  # the text to analyse (call notes until transcripts exist)


class CallReader(Protocol):
    def get_visible(self, principal: Principal, call_id: UUID) -> CallSnapshot:
        """The call if the principal may view it; raises NotFoundError otherwise."""
        ...

    def text_for(self, call_id: UUID) -> str | None:
        """System read for workers (no principal): the call's current text."""
        ...


class InsightRepository(Protocol):
    def add(self, insight: CallInsight) -> None: ...

    def get(self, insight_id: UUID) -> CallInsight | None: ...

    def latest_for_call(self, call_id: UUID, kind: InsightKind) -> CallInsight | None: ...

    def find_reusable(
        self, call_id: UUID, kind: InsightKind, source_hash: str
    ) -> CallInsight | None:
        """An unfinished or succeeded insight of the same text, if any."""
        ...

    def save(self, insight: CallInsight, expected_version: int) -> None: ...


@dataclass(frozen=True, slots=True)
class AiJobState:
    """The part of an AI Platform job (contract ai.job.v1) the CRM uses."""

    job_id: str
    status: str  # queued | running | succeeded | failed | cancelled
    error_code: str | None = None
    schema_id: str | None = None
    provider: str | None = None
    model: str | None = None
    output: dict[str, Any] | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in ("succeeded", "failed", "cancelled")


class AiPlatformClient(Protocol):
    """Calls the AI Platform as ``subject`` within ``tenant_slug``.

    Raises ``TransientError`` when retrying may help (timeouts, 5xx, 429) and
    ``PermanentError`` when it cannot (rejected request, contract mismatch).
    """

    def submit(
        self,
        *,
        tenant_slug: str,
        subject: str,
        job_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> AiJobState: ...

    def fetch(self, *, tenant_slug: str, subject: str, job_id: str) -> AiJobState: ...


class InsightScheduler(Protocol):
    """Queues worker steps; sends only after the current transaction commits."""

    def schedule_submit(self, *, tenant_slug: str, insight_id: UUID) -> None: ...

    def schedule_poll(
        self, *, tenant_slug: str, insight_id: UUID, delay_seconds: float
    ) -> None: ...
