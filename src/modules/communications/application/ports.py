from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from modules.access_control.domain.rbac import MemberRef, Principal
from modules.communications.domain.conversation import (
    AttachmentInfo,
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
    customer_id: UUID | None = None
    lead_id: UUID | None = None


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

    def mark_failed(self, channel: Channel, external_id: str, code: str | None) -> bool:
        """Mark an outbound message as failed. Returns True if one was found."""
        ...

    def message_id(self, channel: Channel, external_id: str) -> UUID | None: ...

    def has_attachment(self, message_id: UUID) -> bool: ...

    def add_attachment(self, message_id: UUID, info: AttachmentInfo, content: bytes) -> None:
        """Keep a message's file. A message has at most one: a second call changes nothing."""
        ...

    def get_attachment(
        self, conversation_id: UUID, message_id: UUID
    ) -> tuple[AttachmentInfo, bytes] | None: ...

    def list_messages(self, conversation_id: UUID, *, limit: int, offset: int) -> list[Message]: ...

    def recent_messages(self, conversation_id: UUID, *, limit: int) -> list[Message]:
        """The newest ``limit`` messages, oldest first."""
        ...


@dataclass(frozen=True, slots=True)
class OutboundFile:
    content: bytes
    file_name: str
    content_type: str


class ChannelSender(Protocol):
    """Sends an outbound message through a provider. Returns the provider message id."""

    def send(self, *, channel: Channel, to_contact: str, text: str) -> str: ...

    def send_file(
        self, *, channel: Channel, to_contact: str, file: OutboundFile, caption: str
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class ContactLink:
    lead_id: UUID | None = None
    customer_id: UUID | None = None


class ContactLinker(Protocol):
    """Finds the lead or customer behind a contact, capturing a lead if there is none."""

    def link(
        self, *, channel: Channel, external_contact_id: str, contact_name: str | None
    ) -> ContactLink: ...

    def hand_over(
        self, principal: Principal, lead_id: UUID, owner_user_id: UUID, team_id: UUID | None
    ) -> None:
        """The chat's lead follows the chat to its new owner."""
        ...


class MemberDirectory(Protocol):
    """Active members of the company (a chat can be handed only to one of them)."""

    def active_member(self, user_id: UUID) -> MemberRef | None: ...


class VoiceEncoder(Protocol):
    def encode(self, content: bytes) -> bytes:
        """A recording as Ogg Opus, the format WhatsApp plays as a voice message."""
        ...


class MediaFetcher(Protocol):
    def fetch(self, url: str) -> bytes | None:
        """The file, or None if it cannot be had at all (a refused host, too large, gone).
        Raises TransientError when trying again later may work."""
        ...
