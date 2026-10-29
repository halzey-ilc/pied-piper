from __future__ import annotations

from dataclasses import asdict
from typing import Any

from rest_framework import serializers

from modules.risk.application.service import RiskReport
from modules.risk.domain.rules import _LIMITS, SignalKind, SubjectType, Thresholds

_HELP = {
    "window_days": "How many past days the rules look at.",
    "amount_multiplier": "An open deal this many times the median open deal is flagged.",
    "disqualify_per_day": "Leads disqualified by one person on one day before it is flagged.",
    "lost_per_day": "Deals marked lost by one person on one day before it is flagged.",
    "voids_per_window": "Invoices voided by one person within the window before it is flagged.",
}


class RiskThresholdsSerializer(serializers.Serializer[dict[str, Any]]):
    window_days = serializers.IntegerField(
        min_value=_LIMITS["window_days"][0],
        max_value=_LIMITS["window_days"][1],
        help_text=_HELP["window_days"],
    )
    amount_multiplier = serializers.IntegerField(
        min_value=_LIMITS["amount_multiplier"][0],
        max_value=_LIMITS["amount_multiplier"][1],
        help_text=_HELP["amount_multiplier"],
    )
    disqualify_per_day = serializers.IntegerField(
        min_value=_LIMITS["disqualify_per_day"][0],
        max_value=_LIMITS["disqualify_per_day"][1],
        help_text=_HELP["disqualify_per_day"],
    )
    lost_per_day = serializers.IntegerField(
        min_value=_LIMITS["lost_per_day"][0],
        max_value=_LIMITS["lost_per_day"][1],
        help_text=_HELP["lost_per_day"],
    )
    voids_per_window = serializers.IntegerField(
        min_value=_LIMITS["voids_per_window"][0],
        max_value=_LIMITS["voids_per_window"][1],
        help_text=_HELP["voids_per_window"],
    )


class SignalSerializer(serializers.Serializer[dict[str, Any]]):
    kind = serializers.ChoiceField(choices=[k.value for k in SignalKind], read_only=True)
    subject_type = serializers.ChoiceField(choices=[t.value for t in SubjectType], read_only=True)
    subject_id = serializers.UUIDField(read_only=True)
    ref = serializers.CharField(read_only=True, help_text="'deal:<uuid>' or 'user:<uuid>'.")
    value = serializers.FloatField(read_only=True, help_text="The measured count or ratio.")
    threshold = serializers.FloatField(read_only=True)
    on = serializers.DateField(read_only=True)
    actor_user_id = serializers.UUIDField(read_only=True, allow_null=True)


class RiskReportSerializer(serializers.Serializer[dict[str, Any]]):
    generated_at = serializers.DateTimeField(read_only=True)
    thresholds = RiskThresholdsSerializer(read_only=True)
    counts = serializers.DictField(child=serializers.IntegerField(), read_only=True)
    signals = SignalSerializer(many=True, read_only=True)
    truncated = serializers.BooleanField(read_only=True)
    is_evidence = serializers.BooleanField(
        read_only=True,
        help_text="Always false: a signal is a reason to check, not proof of a violation.",
    )


def thresholds_to_dict(t: Thresholds) -> dict[str, Any]:
    return asdict(t)


def report_to_dict(report: RiskReport) -> dict[str, Any]:
    return {
        "generated_at": report.generated_at.isoformat(),
        "thresholds": thresholds_to_dict(report.thresholds),
        "counts": report.counts,
        "signals": [
            {
                "kind": s.kind.value,
                "subject_type": s.subject_type.value,
                "subject_id": str(s.subject_id),
                "ref": s.ref,
                "value": s.value,
                "threshold": s.threshold,
                "on": s.on.isoformat(),
                "actor_user_id": str(s.actor_user_id) if s.actor_user_id else None,
            }
            for s in report.signals
        ],
        "truncated": report.truncated,
        "is_evidence": False,
    }
