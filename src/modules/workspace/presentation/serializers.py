from __future__ import annotations

from typing import Any

from rest_framework import serializers

from modules.workspace.application.ports import StoredLayout
from modules.workspace.domain.layout import BLOCK_IDS


class BlockSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.ChoiceField(choices=list(BLOCK_IDS))
    visible = serializers.BooleanField()


class LayoutUpdateSerializer(serializers.Serializer[dict[str, Any]]):
    blocks = BlockSerializer(many=True, allow_empty=False)


class LayoutReadSerializer(serializers.Serializer[dict[str, Any]]):
    department_id = serializers.UUIDField(read_only=True, allow_null=True)
    blocks = BlockSerializer(many=True, read_only=True)
    known_blocks = serializers.ListField(child=serializers.CharField(), read_only=True)
    is_default = serializers.BooleanField(read_only=True)
    updated_by = serializers.UUIDField(read_only=True, allow_null=True)
    updated_at = serializers.DateTimeField(read_only=True, allow_null=True)


def layout_to_dict(stored: StoredLayout) -> dict[str, Any]:
    return {
        "department_id": str(stored.department_id) if stored.department_id else None,
        "blocks": [{"id": b.id, "visible": b.visible} for b in stored.layout.blocks],
        "known_blocks": list(BLOCK_IDS),
        "is_default": stored.updated_at is None,
        "updated_by": str(stored.updated_by) if stored.updated_by else None,
        "updated_at": stored.updated_at.isoformat() if stored.updated_at else None,
    }
