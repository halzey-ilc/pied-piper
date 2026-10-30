"""Input validation and output shaping only; business rules live in the domain."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from rest_framework import serializers

from modules.planning.domain.absence import Absence, AbsenceKind
from modules.planning.domain.plan import Plan, PlanChange, PlanMetric, PlanScope


class PlanSetSerializer(serializers.Serializer[dict[str, Any]]):
    scope = serializers.ChoiceField(choices=[s.value for s in PlanScope])
    scope_id = serializers.UUIDField(required=False, allow_null=True)
    metric = serializers.ChoiceField(choices=[m.value for m in PlanMetric])
    period = serializers.CharField(max_length=10)
    target = serializers.DecimalField(max_digits=16, decimal_places=2, min_value=Decimal(0))


class PlanReadSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    scope = serializers.CharField(read_only=True)
    scope_id = serializers.UUIDField(read_only=True, allow_null=True)
    metric = serializers.CharField(read_only=True)
    period = serializers.CharField(read_only=True)
    target = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class PlanListSerializer(serializers.Serializer[dict[str, Any]]):
    count = serializers.IntegerField(read_only=True)
    results = PlanReadSerializer(many=True, read_only=True)


class PlanChangeReadSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    author_user_id = serializers.UUIDField(read_only=True)
    old_target = serializers.DecimalField(
        max_digits=16, decimal_places=2, read_only=True, allow_null=True
    )
    new_target = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class PlanChangeListSerializer(serializers.Serializer[dict[str, Any]]):
    count = serializers.IntegerField(read_only=True)
    results = PlanChangeReadSerializer(many=True, read_only=True)


class AbsenceCreateSerializer(serializers.Serializer[dict[str, Any]]):
    user_id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=[k.value for k in AbsenceKind])
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    comment = serializers.CharField(required=False, allow_blank=True, max_length=500)


class AbsenceReadSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    user_id = serializers.UUIDField(read_only=True)
    kind = serializers.CharField(read_only=True)
    start_date = serializers.DateField(read_only=True)
    end_date = serializers.DateField(read_only=True)
    comment = serializers.CharField(read_only=True, allow_null=True)
    created_by = serializers.UUIDField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class AbsenceListSerializer(serializers.Serializer[dict[str, Any]]):
    count = serializers.IntegerField(read_only=True)
    results = AbsenceReadSerializer(many=True, read_only=True)


def plan_to_dict(plan: Plan) -> dict[str, Any]:
    return {
        "id": str(plan.id),
        "scope": plan.scope.value,
        "scope_id": str(plan.scope_id) if plan.scope_id else None,
        "metric": plan.metric.value,
        "period": plan.period,
        "target": str(plan.target),
        "created_at": plan.created_at.isoformat(),
        "updated_at": plan.updated_at.isoformat(),
    }


def plan_change_to_dict(change: PlanChange) -> dict[str, Any]:
    return {
        "id": str(change.id),
        "author_user_id": str(change.author_user_id),
        "old_target": str(change.old_target) if change.old_target is not None else None,
        "new_target": str(change.new_target),
        "created_at": change.created_at.isoformat(),
    }


def absence_to_dict(absence: Absence) -> dict[str, Any]:
    return {
        "id": str(absence.id),
        "user_id": str(absence.user_id),
        "kind": absence.kind.value,
        "start_date": absence.start_date.isoformat(),
        "end_date": absence.end_date.isoformat(),
        "comment": absence.comment,
        "created_by": str(absence.created_by),
        "created_at": absence.created_at.isoformat(),
    }
