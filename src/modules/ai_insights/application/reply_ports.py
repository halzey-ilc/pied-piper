from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from modules.access_control.domain.rbac import Principal
from modules.ai_insights.domain.reply import MessageSnapshot, ReplySuggestion


@dataclass(frozen=True, slots=True)
class ConversationSnapshot:
    """What this module needs to know about a conversation."""

    id: UUID
    owner_user_id: UUID | None
    team_id: UUID | None
    channel: str


class ConversationReader(Protocol):
    def get_visible(self, principal: Principal, conversation_id: UUID) -> ConversationSnapshot:
        """The conversation if the principal may view it; raises NotFoundError otherwise."""
        ...

    def channel_of(self, conversation_id: UUID) -> str | None:
        """System read for workers (no principal)."""
        ...

    def recent_messages(self, conversation_id: UUID, limit: int) -> list[MessageSnapshot]:
        """System read for workers: the newest messages, oldest first."""
        ...


class ReplyRepository(Protocol):
    def add(self, suggestion: ReplySuggestion) -> None: ...

    def get(self, suggestion_id: UUID) -> ReplySuggestion | None: ...

    def latest_for_conversation(self, conversation_id: UUID) -> ReplySuggestion | None: ...

    def find_reusable(self, conversation_id: UUID, source_hash: str) -> ReplySuggestion | None:
        """An unfinished or succeeded suggestion for the same messages, if any."""
        ...

    def save(self, suggestion: ReplySuggestion, expected_version: int) -> None: ...


class ReplyScheduler(Protocol):
    """Queues worker steps; sends only after the current transaction commits."""

    def schedule_submit(self, *, tenant_slug: str, suggestion_id: UUID) -> None: ...

    def schedule_poll(
        self, *, tenant_slug: str, suggestion_id: UUID, delay_seconds: float
    ) -> None: ...
