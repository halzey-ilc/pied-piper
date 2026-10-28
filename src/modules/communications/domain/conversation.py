"""Conversation and Message. Pure Python: no Django, no ORM.

A conversation is a thread with one external contact on one channel. Messages
belong to it; the aggregate keeps the thread consistent (last activity, unread).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID, uuid4

from shared.domain.errors import ConflictError, DomainValidationError
from shared.domain.events import DomainEvent

MAX_TEXT_LENGTH = 4096
MAX_NAME_LENGTH = 200


class Channel(StrEnum):
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    INSTAGRAM = "instagram"
    EMAIL = "email"


class ConversationStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class MessageDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageStatus(StrEnum):
    RECEIVED = "received"
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class ConversationStateError(ConflictError):
    code = "conversation_invalid_state"


def _clean_text(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        raise DomainValidationError("Message text must not be empty.")
    if len(cleaned) > MAX_TEXT_LENGTH:
        raise DomainValidationError(f"Message text must be at most {MAX_TEXT_LENGTH} characters.")
    return cleaned


@dataclass(frozen=True, kw_only=True)
class MessageReceived(DomainEvent):
    event_type: ClassVar[str] = "communications.message_received"
    conversation_id: UUID
    channel: str

    def payload(self) -> dict[str, Any]:
        return {"conversation_id": str(self.conversation_id), "channel": self.channel}


@dataclass(frozen=True, kw_only=True)
class MessageSent(DomainEvent):
    event_type: ClassVar[str] = "communications.message_sent"
    conversation_id: UUID
    channel: str

    def payload(self) -> dict[str, Any]:
        return {"conversation_id": str(self.conversation_id), "channel": self.channel}


@dataclass(slots=True)
class Message:
    id: UUID
    direction: MessageDirection
    text: str
    status: MessageStatus
    created_at: datetime
    external_id: str | None = None
    sender_user_id: UUID | None = None


@dataclass(slots=True)
class Conversation:
    id: UUID
    channel: Channel
    external_contact_id: str
    owner_user_id: UUID | None = None
    status: ConversationStatus = ConversationStatus.OPEN
    contact_name: str | None = None
    customer_id: UUID | None = None
    team_id: UUID | None = None
    last_message_at: datetime | None = None
    unread_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    version: int = 1

    @classmethod
    def start(
        cls,
        *,
        channel: Channel,
        external_contact_id: str,
        owner_user_id: UUID | None = None,
        contact_name: str | None = None,
        created_at: datetime,
    ) -> Conversation:
        if not external_contact_id.strip():
            raise DomainValidationError("An external contact id is required.")
        return cls(
            id=uuid4(),
            channel=channel,
            external_contact_id=external_contact_id.strip(),
            owner_user_id=owner_user_id,
            contact_name=(contact_name or "").strip()[:MAX_NAME_LENGTH] or None,
            created_at=created_at,
        )

    def receive(
        self, *, text: str, external_id: str | None, occurred_at: datetime
    ) -> tuple[Message, MessageReceived]:
        message = Message(
            id=uuid4(),
            direction=MessageDirection.INBOUND,
            text=_clean_text(text),
            status=MessageStatus.RECEIVED,
            created_at=occurred_at,
            external_id=external_id,
        )
        self.status = ConversationStatus.OPEN
        self.last_message_at = occurred_at
        self.unread_count += 1
        self.version += 1
        return message, MessageReceived(conversation_id=self.id, channel=self.channel.value)

    def send(
        self, *, text: str, sender_user_id: UUID, occurred_at: datetime
    ) -> tuple[Message, MessageSent]:
        if self.status is ConversationStatus.CLOSED:
            raise ConversationStateError("Reopen the conversation before sending a message.")
        message = Message(
            id=uuid4(),
            direction=MessageDirection.OUTBOUND,
            text=_clean_text(text),
            status=MessageStatus.PENDING,
            created_at=occurred_at,
            sender_user_id=sender_user_id,
        )
        self.last_message_at = occurred_at
        self.version += 1
        return message, MessageSent(conversation_id=self.id, channel=self.channel.value)

    def assign(self, user_id: UUID) -> None:
        self.owner_user_id = user_id
        self.version += 1

    def mark_read(self) -> bool:
        if self.unread_count == 0:
            return False
        self.unread_count = 0
        self.version += 1
        return True

    def close(self) -> None:
        if self.status is ConversationStatus.CLOSED:
            raise ConversationStateError("The conversation is already closed.")
        self.status = ConversationStatus.CLOSED
        self.version += 1

    def reopen(self) -> None:
        if self.status is ConversationStatus.OPEN:
            raise ConversationStateError("The conversation is already open.")
        self.status = ConversationStatus.OPEN
        self.version += 1
