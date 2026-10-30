"""Input validation and output shaping only; business rules live in the domain."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from rest_framework import serializers

from modules.sales_pipeline.domain.deal import MAX_TITLE_LENGTH, Deal
from modules.sales_pipeline.domain.loss_reason import LossReason
from modules.sales_pipeline.domain.pipeline import MAX_STAGES, Pipeline, StageKind
from modules.sales_pipeline.domain.stage_change import StageChange

# --- pipelines --------------------------------------------------------------


class StageDefinitionSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(max_length=100)
    probability = serializers.IntegerField(min_value=0, max_value=100, default=0)
    kind = serializers.ChoiceField(
        choices=[k.value for k in StageKind], default=StageKind.OPEN.value
    )


class PipelineCreateSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(max_length=100)
    is_default = serializers.BooleanField(default=False)
    # many=True forwards min_length/max_length to the generated ListSerializer (stubs miss this).
    stages = StageDefinitionSerializer(  # type: ignore[call-arg]
        many=True, min_length=1, max_length=MAX_STAGES
    )


class StageReadSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    position = serializers.IntegerField(read_only=True)
    probability = serializers.IntegerField(read_only=True)
    kind = serializers.CharField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)


class PipelineReadSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    is_default = serializers.BooleanField(read_only=True)
    stages = StageReadSerializer(many=True, read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


# --- deals ------------------------------------------------------------------


class DealCreateSerializer(serializers.Serializer[dict[str, Any]]):
    title = serializers.CharField(max_length=MAX_TITLE_LENGTH)
    amount = serializers.DecimalField(max_digits=16, decimal_places=2, min_value=Decimal(0))
    currency = serializers.CharField(
        max_length=3, required=False, help_text="Default: the company currency"
    )
    pipeline_id = serializers.UUIDField(required=False)
    customer_id = serializers.UUIDField(required=False, allow_null=True)
    expected_close_date = serializers.DateField(required=False, allow_null=True)


class DealUpdateSerializer(serializers.Serializer[dict[str, Any]]):
    title = serializers.CharField(required=False, max_length=MAX_TITLE_LENGTH)
    amount = serializers.DecimalField(
        required=False, max_digits=16, decimal_places=2, min_value=Decimal(0)
    )
    currency = serializers.CharField(required=False, max_length=3)
    customer_id = serializers.UUIDField(required=False, allow_null=True)
    expected_close_date = serializers.DateField(required=False, allow_null=True)
    version = serializers.IntegerField(required=False, min_value=1)


class DealMoveSerializer(serializers.Serializer[dict[str, Any]]):
    stage_id = serializers.UUIDField()
    # Required only when the target stage is a lost-kind stage: a catalog
    # loss_reason_id and/or a free-text reason.
    reason = serializers.CharField(required=False, max_length=500, allow_blank=False)
    loss_reason_id = serializers.UUIDField(required=False, allow_null=True)


class DealLoseSerializer(serializers.Serializer[dict[str, Any]]):
    reason = serializers.CharField(max_length=500)


# --- loss reasons -----------------------------------------------------------


class LossReasonCreateSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(max_length=100)


class LossReasonUpdateSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(required=False, max_length=100)
    is_active = serializers.BooleanField(required=False)


class LossReasonReadSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class LossReasonListSerializer(serializers.Serializer[dict[str, Any]]):
    count = serializers.IntegerField(read_only=True)
    results = LossReasonReadSerializer(many=True, read_only=True)


# --- stage history ----------------------------------------------------------


class StageChangeReadSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    from_stage_id = serializers.UUIDField(read_only=True, allow_null=True)
    to_stage_id = serializers.UUIDField(read_only=True)
    author_user_id = serializers.UUIDField(read_only=True)
    deal_version = serializers.IntegerField(read_only=True)
    reason = serializers.CharField(read_only=True, allow_null=True)
    loss_reason_id = serializers.UUIDField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)


class StageChangeListSerializer(serializers.Serializer[dict[str, Any]]):
    count = serializers.IntegerField(read_only=True)
    results = StageChangeReadSerializer(many=True, read_only=True)


class DealReadSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    title = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    amount = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    currency = serializers.CharField(read_only=True)
    pipeline_id = serializers.UUIDField(read_only=True)
    stage_id = serializers.UUIDField(read_only=True)
    customer_id = serializers.UUIDField(read_only=True, allow_null=True)
    owner_user_id = serializers.UUIDField(read_only=True)
    expected_close_date = serializers.DateField(read_only=True, allow_null=True)
    closed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    lost_reason = serializers.CharField(read_only=True, allow_null=True)
    loss_reason_id = serializers.UUIDField(read_only=True, allow_null=True)
    version = serializers.IntegerField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class DealListSerializer(serializers.Serializer[dict[str, Any]]):
    count = serializers.IntegerField(read_only=True)
    results = DealReadSerializer(many=True, read_only=True)


def pipeline_to_dict(pipeline: Pipeline) -> dict[str, Any]:
    return {
        "id": str(pipeline.id),
        "name": pipeline.name,
        "is_default": pipeline.is_default,
        "stages": [
            {
                "id": str(stage.id),
                "name": stage.name,
                "position": stage.position,
                "probability": stage.probability,
                "kind": stage.kind.value,
                "is_active": stage.is_active,
            }
            for stage in sorted(pipeline.stages, key=lambda s: s.position)
        ],
        "created_at": pipeline.created_at.isoformat(),
    }


def deal_to_dict(deal: Deal) -> dict[str, Any]:
    return {
        "id": str(deal.id),
        "title": deal.title,
        "status": deal.status.value,
        "amount": str(deal.value.amount),
        "currency": deal.value.currency,
        "pipeline_id": str(deal.pipeline_id),
        "stage_id": str(deal.stage_id),
        "customer_id": str(deal.customer_id) if deal.customer_id else None,
        "owner_user_id": str(deal.owner_user_id),
        "expected_close_date": (
            deal.expected_close_date.isoformat() if deal.expected_close_date else None
        ),
        "closed_at": deal.closed_at.isoformat() if deal.closed_at else None,
        "lost_reason": deal.lost_reason,
        "loss_reason_id": str(deal.loss_reason_id) if deal.loss_reason_id else None,
        "version": deal.version,
        "created_at": deal.created_at.isoformat(),
    }


def loss_reason_to_dict(reason: LossReason) -> dict[str, Any]:
    return {
        "id": str(reason.id),
        "name": reason.name,
        "is_active": reason.is_active,
        "created_at": reason.created_at.isoformat(),
    }


def stage_change_to_dict(change: StageChange) -> dict[str, Any]:
    return {
        "id": str(change.id),
        "from_stage_id": str(change.from_stage_id) if change.from_stage_id else None,
        "to_stage_id": str(change.to_stage_id),
        "author_user_id": str(change.author_user_id),
        "deal_version": change.deal_version,
        "reason": change.reason,
        "loss_reason_id": str(change.loss_reason_id) if change.loss_reason_id else None,
        "created_at": change.created_at.isoformat(),
    }
