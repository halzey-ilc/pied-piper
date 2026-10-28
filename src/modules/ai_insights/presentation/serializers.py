from __future__ import annotations

from typing import Any

from rest_framework import serializers

from modules.ai_insights.domain.insight import CallInsight


class CallInsightSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    call_id = serializers.UUIDField(read_only=True)
    kind = serializers.CharField(read_only=True)
    status = serializers.CharField(
        read_only=True, help_text="pending | submitted | succeeded | failed"
    )
    is_mock = serializers.BooleanField(
        read_only=True,
        help_text="True when the result came from the AI Platform's mock provider, not a model.",
    )
    provider = serializers.CharField(read_only=True, allow_null=True)
    model = serializers.CharField(read_only=True, allow_null=True)
    schema_id = serializers.CharField(read_only=True, allow_null=True)
    result = serializers.JSONField(
        read_only=True,
        allow_null=True,
        help_text="Structured output (call_summary.output.v1); every item cites the call text.",
    )
    error_code = serializers.CharField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    finished_at = serializers.DateTimeField(read_only=True, allow_null=True)


def insight_to_dict(insight: CallInsight) -> dict[str, Any]:
    return {
        "id": str(insight.id),
        "call_id": str(insight.call_id),
        "kind": insight.kind.value,
        "status": insight.status.value,
        "is_mock": insight.is_mock,
        "provider": insight.provider,
        "model": insight.model,
        "schema_id": insight.schema_id,
        "result": insight.result,
        "error_code": insight.error_code,
        "created_at": insight.created_at.isoformat(),
        "finished_at": insight.finished_at.isoformat() if insight.finished_at else None,
    }
