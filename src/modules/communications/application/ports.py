from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from modules.communications.domain.conversation import (
    Channel,
    Conversation,
    ConversationStatus,
    Message,
)


@dataclass(frozen=True, slots=True)
class ConversationVisibility:
    owner_user_id: UUID | None = None
    team_ids: frozenset[UUID] = field(default_factory=frozenset)
    include_unassigned: bool = False


@dataclass(frozen=True, slots=True)
class ConversationFilter:
    channel: Channel | None = None
    status: ConversationStatus | None = None


class ConversationRepository(Protocol):
    def get(self, conversation_id: UUID) -> Conversation | None: ...

    def get_by_contact(self, channel: Channel, external_contact_id: str) -> Conversation | None: ...

    def add(self, conversation: Conversation) -> None: ...

    def save(self, conversation: Conversation, expected_version: int) -> None: ...

    def list_visible(
        self,
        visibility: ConversationVisibility,
        filters: ConversationFilter,
        *,
        limit: int,
        offset: int,
    ) -> list[Conversation]: ...

    def count(self, visibility: ConversationVisibility, filters: ConversationFilter) -> int: ...

    def add_message(self, conversation_id: UUID, message: Message) -> None: ...

    def message_exists(self, channel: Channel, external_id: str) -> bool: ...

    def list_messages(self, conversation_id: UUID, *, limit: int, offset: int) -> list[Message]: ...


class ChannelSender(Protocol):
    """Sends an outbound message through a provider. Returns the provider message id.

    Not wired to real accounts in the scaffold; a stub returns a synthetic id.
    """

    def send(self, *, channel: Channel, to_contact: str, text: str) -> str: ...
