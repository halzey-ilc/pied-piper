from __future__ import annotations

from typing import Any

from rest_framework import serializers

from modules.communications.domain.conversation import Channel, Conversation, Message


class SendMessageSerializer(serializers.Serializer[dict[str, Any]]):
    text = serializers.CharField(max_length=4096)


class MessageReadSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    direction = serializers.CharField(read_only=True)
    text = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    external_id = serializers.CharField(read_only=True, allow_null=True)
    sender_user_id = serializers.UUIDField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)


class ConversationReadSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    channel = serializers.CharField(read_only=True)
    external_contact_id = serializers.CharField(read_only=True)
    contact_name = serializers.CharField(read_only=True, allow_null=True)
    status = serializers.CharField(read_only=True)
    customer_id = serializers.UUIDField(read_only=True, allow_null=True)
    owner_user_id = serializers.UUIDField(read_only=True, allow_null=True)
    unread_count = serializers.IntegerField(read_only=True)
    last_message_at = serializers.DateTimeField(read_only=True, allow_null=True)
    version = serializers.IntegerField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class ConversationListSerializer(serializers.Serializer[dict[str, Any]]):
    count = serializers.IntegerField(read_only=True)
    results = ConversationReadSerializer(many=True, read_only=True)


class MessageListSerializer(serializers.Serializer[dict[str, Any]]):
    results = MessageReadSerializer(many=True, read_only=True)


def conversation_to_dict(c: Conversation) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "channel": c.channel.value,
        "external_contact_id": c.external_contact_id,
        "contact_name": c.contact_name,
        "status": c.status.value,
        "customer_id": str(c.customer_id) if c.customer_id else None,
        "owner_user_id": str(c.owner_user_id) if c.owner_user_id else None,
        "unread_count": c.unread_count,
        "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
        "version": c.version,
        "created_at": c.created_at.isoformat(),
    }


def message_to_dict(m: Message) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "direction": m.direction.value,
        "text": m.text,
        "status": m.status.value,
        "external_id": m.external_id,
        "sender_user_id": str(m.sender_user_id) if m.sender_user_id else None,
        "created_at": m.created_at.isoformat(),
    }


_CHANNELS = [c.value for c in Channel]
