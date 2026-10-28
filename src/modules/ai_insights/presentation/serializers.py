from __future__ import annotations

from typing import Any

from rest_framework import serializers

from modules.ai_insights.domain.digest import LANGUAGES, OperationsDigest
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


class DigestRequestSerializer(serializers.Serializer[dict[str, Any]]):
    language = serializers.ChoiceField(choices=list(LANGUAGES), required=False)


class OperationsDigestSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    trigger = serializers.CharField(read_only=True, help_text="manual | daily")
    digest_date = serializers.DateField(read_only=True)
    status = serializers.CharField(
        read_only=True, help_text="pending | submitted | succeeded | failed"
    )
    counts = serializers.DictField(
        child=serializers.IntegerField(), read_only=True, help_text="Findings sent, by kind."
    )
    is_mock = serializers.BooleanField(read_only=True)
    provider = serializers.CharField(read_only=True, allow_null=True)
    model = serializers.CharField(read_only=True, allow_null=True)
    result = serializers.JSONField(
        read_only=True,
        allow_null=True,
        help_text=(
            "operations_digest.output.v1. finding_refs look like 'lead:<uuid>', "
            "'deal:<uuid>' or 'task:<uuid>'."
        ),
    )
    error_code = serializers.CharField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    finished_at = serializers.DateTimeField(read_only=True, allow_null=True)


def digest_to_dict(digest: OperationsDigest) -> dict[str, Any]:
    return {
        "id": str(digest.id),
        "trigger": digest.trigger.value,
        "digest_date": digest.digest_date.isoformat(),
        "status": digest.status.value,
        "counts": digest.payload.get("counts", {}),
        "is_mock": digest.is_mock,
        "provider": digest.provider,
        "model": digest.model,
        "result": digest.result,
        "error_code": digest.error_code,
        "created_at": digest.created_at.isoformat(),
        "finished_at": digest.finished_at.isoformat() if digest.finished_at else None,
    }


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
