from __future__ import annotations

from typing import Any

from rest_framework import serializers

from modules.ai_insights.domain.insight import CallInsight
from modules.ai_insights.domain.kpi_payload import PERIODS
from modules.ai_insights.domain.reply import ReplySuggestion
from modules.ai_insights.domain.report import LANGUAGES, AiReport, ReportKind


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
    summary_grounded = serializers.BooleanField(
        read_only=True,
        help_text=(
            "Always false: result.summary is free text the AI wrote and is not checked against "
            "the call. key_points and next_step are, by verbatim quotes. Show those first; "
            "measured summaries in Kyrgyz contained factual errors."
        ),
    )
    error_code = serializers.CharField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    finished_at = serializers.DateTimeField(read_only=True, allow_null=True)


class DigestRequestSerializer(serializers.Serializer[dict[str, Any]]):
    language = serializers.ChoiceField(choices=list(LANGUAGES), required=False)


class KpiExplanationRequestSerializer(serializers.Serializer[dict[str, Any]]):
    period = serializers.ChoiceField(choices=list(PERIODS), default="month")
    date = serializers.DateField(
        required=False, help_text="Any day of the period to explain (default: today)."
    )
    language = serializers.ChoiceField(choices=list(LANGUAGES), required=False)


class AiReportSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    kind = serializers.ChoiceField(choices=[k.value for k in ReportKind], read_only=True)
    trigger = serializers.CharField(read_only=True, help_text="manual | daily")
    report_date = serializers.DateField(read_only=True)
    status = serializers.CharField(
        read_only=True, help_text="pending | submitted | succeeded | failed"
    )
    input = serializers.JSONField(
        read_only=True,
        help_text=(
            "What was sent to the AI: ids, kinds and CRM-computed numbers only. For KPI "
            "explanations this is the source of truth for every number in the result."
        ),
    )
    is_mock = serializers.BooleanField(read_only=True)
    provider = serializers.CharField(read_only=True, allow_null=True)
    model = serializers.CharField(read_only=True, allow_null=True)
    result = serializers.JSONField(
        read_only=True,
        allow_null=True,
        help_text=(
            "operations_digest.output.v1 (finding_refs like 'lead:<uuid>') or "
            "kpi_explanation.output.v1 (metric_codes)."
        ),
    )
    error_code = serializers.CharField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    finished_at = serializers.DateTimeField(read_only=True, allow_null=True)


class ReplyRequestSerializer(serializers.Serializer[dict[str, Any]]):
    language = serializers.ChoiceField(
        choices=list(LANGUAGES), required=False, help_text="Language of the draft."
    )


class ReplySuggestionSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    conversation_id = serializers.UUIDField(read_only=True)
    status = serializers.CharField(
        read_only=True, help_text="pending | submitted | succeeded | failed"
    )
    language = serializers.CharField(read_only=True)
    is_mock = serializers.BooleanField(read_only=True)
    provider = serializers.CharField(read_only=True, allow_null=True)
    model = serializers.CharField(read_only=True, allow_null=True)
    result = serializers.JSONField(
        read_only=True,
        allow_null=True,
        help_text=(
            "message_reply.output.v1: intent, urgency, evidence (a verbatim quote of a customer "
            "message), draft_reply (null when no safe draft exists) and needs_human. The draft "
            "has no numbers, links or e-mails that are not in the conversation."
        ),
    )
    auto_send = serializers.BooleanField(
        read_only=True,
        help_text="Always false: the draft is never sent; a person sends their own message.",
    )
    error_code = serializers.CharField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    finished_at = serializers.DateTimeField(read_only=True, allow_null=True)


def reply_to_dict(suggestion: ReplySuggestion) -> dict[str, Any]:
    return {
        "id": str(suggestion.id),
        "conversation_id": str(suggestion.conversation_id),
        "status": suggestion.status.value,
        "language": suggestion.language,
        "is_mock": suggestion.is_mock,
        "provider": suggestion.provider,
        "model": suggestion.model,
        "result": suggestion.result,
        "auto_send": False,
        "error_code": suggestion.error_code,
        "created_at": suggestion.created_at.isoformat(),
        "finished_at": suggestion.finished_at.isoformat() if suggestion.finished_at else None,
    }


def report_to_dict(report: AiReport) -> dict[str, Any]:
    return {
        "id": str(report.id),
        "kind": report.kind.value,
        "trigger": report.trigger.value,
        "report_date": report.report_date.isoformat(),
        "status": report.status.value,
        "input": report.payload,
        "is_mock": report.is_mock,
        "provider": report.provider,
        "model": report.model,
        "result": report.result,
        "error_code": report.error_code,
        "created_at": report.created_at.isoformat(),
        "finished_at": report.finished_at.isoformat() if report.finished_at else None,
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
        "summary_grounded": False,
        "error_code": insight.error_code,
        "created_at": insight.created_at.isoformat(),
        "finished_at": insight.finished_at.isoformat() if insight.finished_at else None,
    }
