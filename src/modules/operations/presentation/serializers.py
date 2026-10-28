from __future__ import annotations

from typing import Any

from rest_framework import serializers

from modules.operations.application.service import OperationsReport
from modules.operations.domain.rules import MAX_THRESHOLD_DAYS, FindingKind, RecordType, Thresholds


class ThresholdsSerializer(serializers.Serializer[dict[str, Any]]):
    stale_lead_days = serializers.IntegerField(
        min_value=1,
        max_value=MAX_THRESHOLD_DAYS,
        help_text="A new/qualified lead untouched this many days counts as forgotten.",
    )
    stalled_deal_days = serializers.IntegerField(
        min_value=1,
        max_value=MAX_THRESHOLD_DAYS,
        help_text="An open deal untouched this many days counts as stalled.",
    )


class FindingSerializer(serializers.Serializer[dict[str, Any]]):
    kind = serializers.ChoiceField(choices=[k.value for k in FindingKind], read_only=True)
    record_type = serializers.ChoiceField(choices=[t.value for t in RecordType], read_only=True)
    record_id = serializers.UUIDField(read_only=True)
    owner_user_id = serializers.UUIDField(read_only=True)
    age_days = serializers.IntegerField(
        read_only=True, help_text="Days since last update (overdue tasks: days past due)."
    )
    deal_id = serializers.UUIDField(read_only=True, allow_null=True)


class OperationsReportSerializer(serializers.Serializer[dict[str, Any]]):
    generated_at = serializers.DateTimeField(read_only=True)
    thresholds = ThresholdsSerializer(read_only=True)
    counts = serializers.DictField(child=serializers.IntegerField(), read_only=True)
    findings = FindingSerializer(many=True, read_only=True)
    truncated = serializers.BooleanField(read_only=True)
    skipped = serializers.ListField(
        child=serializers.CharField(),
        read_only=True,
        help_text="Categories left out because you may not view those records.",
    )


def thresholds_to_dict(t: Thresholds) -> dict[str, Any]:
    return {"stale_lead_days": t.stale_lead_days, "stalled_deal_days": t.stalled_deal_days}


def report_to_dict(report: OperationsReport) -> dict[str, Any]:
    return {
        "generated_at": report.generated_at.isoformat(),
        "thresholds": thresholds_to_dict(report.thresholds),
        "counts": report.counts,
        "findings": [
            {
                "kind": f.kind.value,
                "record_type": f.record_type.value,
                "record_id": str(f.record_id),
                "owner_user_id": str(f.owner_user_id),
                "age_days": f.age_days,
                "deal_id": str(f.deal_id) if f.deal_id else None,
            }
            for f in report.findings
        ],
        "truncated": report.truncated,
        "skipped": list(report.skipped),
    }
