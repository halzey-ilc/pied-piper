from __future__ import annotations

from uuid import UUID

from django.db.models import F, Q, QuerySet

from modules.communications.application.ports import (
    ConversationFilter,
    ConversationVisibility,
)
from modules.communications.domain.conversation import (
    Channel,
    Conversation,
    ConversationStatus,
    Message,
    MessageDirection,
    MessageStatus,
)
from modules.communications.infrastructure.models import ConversationRecord, MessageRecord
from shared.domain.errors import ConflictError


def _conv_to_domain(row: ConversationRecord) -> Conversation:
    return Conversation(
        id=row.id,
        channel=Channel(row.channel),
        external_contact_id=row.external_contact_id,
        owner_user_id=row.owner_user_id,
        status=ConversationStatus(row.status),
        contact_name=row.contact_name,
        customer_id=row.customer_id,
        team_id=row.team_id,
        last_message_at=row.last_message_at,
        unread_count=row.unread_count,
        created_at=row.created_at,
        version=row.version,
    )


def _msg_to_domain(row: MessageRecord) -> Message:
    return Message(
        id=row.id,
        direction=MessageDirection(row.direction),
        text=row.text,
        status=MessageStatus(row.status),
        created_at=row.created_at,
        external_id=row.external_id,
        sender_user_id=row.sender_user_id,
    )


class DjangoConversationRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def _conv(self) -> QuerySet[ConversationRecord]:
        return ConversationRecord.objects.using(self.using)

    def _msg(self) -> QuerySet[MessageRecord]:
        return MessageRecord.objects.using(self.using)

    def get(self, conversation_id: UUID) -> Conversation | None:
        row = self._conv().filter(id=conversation_id).first()
        return None if row is None else _conv_to_domain(row)

    def get_by_contact(self, channel: Channel, external_contact_id: str) -> Conversation | None:
        row = (
            self._conv()
            .filter(channel=channel.value, external_contact_id=external_contact_id)
            .first()
        )
        return None if row is None else _conv_to_domain(row)

    def add(self, conversation: Conversation) -> None:
        self._conv().create(
            id=conversation.id, created_at=conversation.created_at, **self._fields(conversation)
        )

    def save(self, conversation: Conversation, expected_version: int) -> None:
        updated = (
            self._conv()
            .filter(id=conversation.id, version=expected_version)
            .update(version=F("version") + 1, **self._fields(conversation))
        )
        if updated != 1:
            raise ConflictError("Conversation was modified by someone else; reload and retry.")

    def _filtered(
        self, visibility: ConversationVisibility, filters: ConversationFilter
    ) -> QuerySet[ConversationRecord]:
        qs = self._conv()
        if visibility.owner_user_id is not None:
            condition = Q(owner_user_id=visibility.owner_user_id)
            if visibility.team_ids:
                condition |= Q(team_id__in=visibility.team_ids)
            if visibility.include_unassigned:
                condition |= Q(owner_user_id__isnull=True)
            qs = qs.filter(condition)
        if filters.channel is not None:
            qs = qs.filter(channel=filters.channel.value)
        if filters.status is not None:
            qs = qs.filter(status=filters.status.value)
        return qs

    def list_visible(
        self,
        visibility: ConversationVisibility,
        filters: ConversationFilter,
        *,
        limit: int,
        offset: int,
    ) -> list[Conversation]:
        rows = self._filtered(visibility, filters).order_by(
            "-last_message_at", "-created_at", "id"
        )[offset : offset + limit]
        return [_conv_to_domain(row) for row in rows]

    def count(self, visibility: ConversationVisibility, filters: ConversationFilter) -> int:
        return self._filtered(visibility, filters).count()

    def add_message(self, conversation_id: UUID, message: Message) -> None:
        channel = self._conv().filter(id=conversation_id).values_list("channel", flat=True).first()
        if channel is None:  # the conversation is always persisted before its messages
            raise ConflictError("Conversation not found for message.")
        self._msg().create(
            id=message.id,
            conversation_id=conversation_id,
            channel=channel,
            direction=message.direction.value,
            text=message.text,
            status=message.status.value,
            external_id=message.external_id,
            sender_user_id=message.sender_user_id,
            created_at=message.created_at,
        )

    def message_exists(self, channel: Channel, external_id: str) -> bool:
        return self._msg().filter(channel=channel.value, external_id=external_id).exists()

    def list_messages(self, conversation_id: UUID, *, limit: int, offset: int) -> list[Message]:
        rows = (
            self._msg()
            .filter(conversation_id=conversation_id)
            .order_by("created_at", "id")[offset : offset + limit]
        )
        return [_msg_to_domain(row) for row in rows]

    def recent_messages(self, conversation_id: UUID, *, limit: int) -> list[Message]:
        rows = (
            self._msg()
            .filter(conversation_id=conversation_id)
            .order_by("-created_at", "-id")[:limit]
        )
        return [_msg_to_domain(row) for row in reversed(rows)]

    def _fields(self, conversation: Conversation) -> dict[str, object]:
        return {
            "channel": conversation.channel.value,
            "external_contact_id": conversation.external_contact_id,
            "contact_name": conversation.contact_name,
            "status": conversation.status.value,
            "customer_id": conversation.customer_id,
            "owner_user_id": conversation.owner_user_id,
            "team_id": conversation.team_id,
            "last_message_at": conversation.last_message_at,
            "unread_count": conversation.unread_count,
        }
