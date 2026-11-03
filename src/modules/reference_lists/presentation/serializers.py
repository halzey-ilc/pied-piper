from __future__ import annotations

from typing import Any

from rest_framework import serializers

from modules.reference_lists.domain.lists import MAX_LABEL_LENGTH, ListItem, ListKey


class ItemCreateSerializer(serializers.Serializer[dict[str, Any]]):
    label = serializers.CharField(max_length=MAX_LABEL_LENGTH)  # type: ignore[assignment]


class ItemUpdateSerializer(serializers.Serializer[dict[str, Any]]):
    label = serializers.CharField(required=False, max_length=MAX_LABEL_LENGTH)  # type: ignore[assignment]
    is_active = serializers.BooleanField(required=False)


class ReorderSerializer(serializers.Serializer[dict[str, Any]]):
    ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=True, max_length=50)


class ItemReadSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True, allow_null=True)
    code = serializers.CharField(read_only=True, allow_null=True)
    label = serializers.CharField(read_only=True)  # type: ignore[assignment]  # DRF Field.label
    is_active = serializers.BooleanField(read_only=True)
    position = serializers.IntegerField(read_only=True)


class ListReadSerializer(serializers.Serializer[dict[str, Any]]):
    key = serializers.CharField(read_only=True)
    fixed = serializers.BooleanField(read_only=True)
    editable = serializers.BooleanField(read_only=True)
    items = ItemReadSerializer(many=True, read_only=True)


def item_to_dict(item: ListItem) -> dict[str, Any]:
    return {
        "id": str(item.id) if item.id else None,
        "code": item.code,
        "label": item.label,
        "is_active": item.is_active,
        "position": item.position,
    }


def list_to_dict(key: ListKey, items: list[ListItem], *, editable: bool) -> dict[str, Any]:
    return {
        "key": key.value,
        "fixed": key.is_fixed,
        "editable": editable,
        "items": [item_to_dict(item) for item in items],
    }
