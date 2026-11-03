"""Input validation and output shaping only; business rules live in the domain."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from rest_framework import serializers

from modules.activities.presentation.serializers import NextStepSerializer
from modules.sales_pipeline.domain.custom_field import CustomFieldDefinition, CustomFieldType
from modules.sales_pipeline.domain.deal import MAX_TITLE_LENGTH, Deal
from modules.sales_pipeline.domain.loss_reason import LossReason
from modules.sales_pipeline.domain.pipeline import MAX_STAGES, Pipeline, Stage, StageKind
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
    department_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="Director only: owning sales department (omit for company-wide). A head "
        "of sales always creates their own department's pipeline.",
    )
    # many=True forwards min_length/max_length to the generated ListSerializer (stubs miss this).
    stages = StageDefinitionSerializer(  # type: ignore[call-arg]
        many=True, min_length=1, max_length=MAX_STAGES
    )


class PipelineUpdateSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(max_length=100, required=False)
    is_default = serializers.BooleanField(
        required=False, help_text="true makes this the default pipeline (the flag moves here)"
    )

    def validate_is_default(self, value: bool) -> bool:
        if value is False:
            raise serializers.ValidationError("Make another pipeline the default instead.")
        return value


class StageAddSerializer(StageDefinitionSerializer):
    position = serializers.IntegerField(
        min_value=0, required=False, help_text="0-based; omitted = at the end"
    )


class StageUpdateSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(max_length=100, required=False)
    probability = serializers.IntegerField(min_value=0, max_value=100, required=False)
    is_active = serializers.BooleanField(
        required=False, help_text="false archives the stage, true restores it"
    )


class StageOrderSerializer(serializers.Serializer[dict[str, Any]]):
    stage_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1)


class DealAssignSerializer(serializers.Serializer[dict[str, Any]]):
    owner_user_id = serializers.UUIDField()


class StageReadSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    position = serializers.IntegerField(read_only=True)
    probability = serializers.IntegerField(read_only=True)
    kind = serializers.CharField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    system_code = serializers.ChoiceField(
        choices=["sale", "meeting_rescheduled"],
        read_only=True,
        allow_null=True,
        help_text=(
            "System stage: `sale` («Продажа», always last, set only by a confirmed payment) "
            "or `meeting_rescheduled` («Встреча перенесена», can be moved and switched off). "
            "Null for ordinary stages."
        ),
    )
    is_system = serializers.BooleanField(read_only=True)
    open_deals = serializers.IntegerField(
        read_only=True, required=False, help_text="Only with ?counts=true"
    )
    total_deals = serializers.IntegerField(
        read_only=True, required=False, help_text="Only with ?counts=true"
    )


class PipelineReadSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    is_default = serializers.BooleanField(read_only=True)
    department_id = serializers.UUIDField(read_only=True, allow_null=True)
    stages = StageReadSerializer(many=True, read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class StageRemovalSerializer(serializers.Serializer[dict[str, Any]]):
    pipeline = PipelineReadSerializer(read_only=True)
    moved_deals = serializers.IntegerField(read_only=True)
    archived = serializers.BooleanField(
        read_only=True, help_text="true: the stage had deals once, so it is archived, not removed"
    )


class AdoptDealsSerializer(serializers.Serializer[dict[str, Any]]):
    from_pipeline_id = serializers.UUIDField()
    stage_map = serializers.DictField(
        child=serializers.UUIDField(),
        help_text="Source stage id -> target stage id (an active open stage of this pipeline)",
    )


class AdoptionStageSerializer(serializers.Serializer[dict[str, Any]]):
    stage_id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    kind = serializers.CharField(read_only=True)
    open_deals = serializers.IntegerField(read_only=True)


class AdoptionPreviewSerializer(serializers.Serializer[dict[str, Any]]):
    from_pipeline_id = serializers.UUIDField(read_only=True)
    from_pipeline_name = serializers.CharField(read_only=True)
    total = serializers.IntegerField(read_only=True)
    stages = AdoptionStageSerializer(many=True, read_only=True)


class AdoptionResultSerializer(serializers.Serializer[dict[str, Any]]):
    moved = serializers.IntegerField(read_only=True)
    per_stage = serializers.DictField(child=serializers.IntegerField(), read_only=True)


# --- deals ------------------------------------------------------------------


class DealCreateSerializer(serializers.Serializer[dict[str, Any]]):
    title = serializers.CharField(max_length=MAX_TITLE_LENGTH)
    amount = serializers.DecimalField(max_digits=16, decimal_places=2, min_value=Decimal(0))
    currency = serializers.CharField(
        max_length=3, required=False, help_text="Default: the company currency"
    )
    pipeline_id = serializers.UUIDField(required=False)
    customer_id = serializers.UUIDField(required=False, allow_null=True)
    source = serializers.CharField(  # type: ignore[assignment]  # DRF field named "source"
        required=False, allow_null=True, allow_blank=True, max_length=100
    )
    next_step = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, max_length=500
    )
    custom_fields = serializers.DictField(required=False)
    expected_close_date = serializers.DateField(required=False, allow_null=True)


class DealUpdateSerializer(serializers.Serializer[dict[str, Any]]):
    title = serializers.CharField(required=False, max_length=MAX_TITLE_LENGTH)
    amount = serializers.DecimalField(
        required=False, max_digits=16, decimal_places=2, min_value=Decimal(0)
    )
    currency = serializers.CharField(required=False, max_length=3)
    customer_id = serializers.UUIDField(required=False, allow_null=True)
    source = serializers.CharField(  # type: ignore[assignment]  # DRF field named "source"
        required=False, allow_null=True, allow_blank=True, max_length=100
    )
    next_step = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, max_length=500
    )
    custom_fields = serializers.DictField(required=False)
    expected_close_date = serializers.DateField(required=False, allow_null=True)
    version = serializers.IntegerField(required=False, min_value=1)


class DealMoveSerializer(serializers.Serializer[dict[str, Any]]):
    stage_id = serializers.UUIDField()
    # Required only when the target stage is a lost-kind stage: a catalog
    # loss_reason_id and/or a free-text reason.
    reason = serializers.CharField(required=False, max_length=500, allow_blank=False)
    loss_reason_id = serializers.UUIDField(required=False, allow_null=True)
    # «Встреча перенесена»: the new meeting time (400 meeting_date_required without it).
    meeting_start = serializers.DateTimeField(required=False, allow_null=True)
    meeting_end = serializers.DateTimeField(required=False, allow_null=True)
    # Another open stage: required unless the deal has an open task with a due date.
    next_step = NextStepSerializer(required=False, allow_null=True)


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
    source = serializers.CharField(read_only=True, allow_null=True)  # type: ignore[assignment]
    next_step = serializers.CharField(read_only=True, allow_null=True)
    custom_fields = serializers.DictField(read_only=True)
    expected_close_date = serializers.DateField(read_only=True, allow_null=True)
    closed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    lost_reason = serializers.CharField(read_only=True, allow_null=True)
    loss_reason_id = serializers.UUIDField(read_only=True, allow_null=True)
    version = serializers.IntegerField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True, allow_null=True)


class DealCustomerSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True, allow_null=True)
    phone = serializers.CharField(read_only=True, allow_null=True)


class DealOwnerSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)


class DealDetailSerializer(DealReadSerializer):
    """One deal with what its card needs: the linked customer and the owner's name.

    `customer` is null when no customer is linked or the caller cannot view it.
    """

    customer = DealCustomerSerializer(read_only=True, allow_null=True)
    owner = DealOwnerSerializer(read_only=True)


class DealListSerializer(serializers.Serializer[dict[str, Any]]):
    count = serializers.IntegerField(read_only=True)
    results = DealReadSerializer(many=True, read_only=True)


def pipeline_to_dict(
    pipeline: Pipeline, counts: dict[UUID, tuple[int, int]] | None = None
) -> dict[str, Any]:
    def stage_dict(stage: Stage) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": str(stage.id),
            "name": stage.name,
            "position": stage.position,
            "probability": stage.probability,
            "kind": stage.kind.value,
            "is_active": stage.is_active,
            "system_code": stage.system_code.value if stage.system_code else None,
            "is_system": stage.is_system,
        }
        if counts is not None:
            open_deals, total_deals = counts.get(stage.id, (0, 0))
            data["open_deals"] = open_deals
            data["total_deals"] = total_deals
        return data

    return {
        "id": str(pipeline.id),
        "name": pipeline.name,
        "is_default": pipeline.is_default,
        "department_id": str(pipeline.department_id) if pipeline.department_id else None,
        "stages": [
            stage_dict(stage) for stage in sorted(pipeline.stages, key=lambda s: s.position)
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
        "source": deal.source,
        "next_step": deal.next_step,
        "custom_fields": deal.custom_fields,
        "expected_close_date": (
            deal.expected_close_date.isoformat() if deal.expected_close_date else None
        ),
        "closed_at": deal.closed_at.isoformat() if deal.closed_at else None,
        "lost_reason": deal.lost_reason,
        "loss_reason_id": str(deal.loss_reason_id) if deal.loss_reason_id else None,
        "version": deal.version,
        "created_at": deal.created_at.isoformat(),
        "updated_at": deal.updated_at.isoformat() if deal.updated_at else None,
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


# --- custom field definitions -----------------------------------------------


class CustomFieldCreateSerializer(serializers.Serializer[dict[str, Any]]):
    key = serializers.CharField(max_length=50)
    label = serializers.CharField(max_length=100)  # type: ignore[assignment]  # DRF Field.label
    field_type = serializers.ChoiceField(choices=[t.value for t in CustomFieldType])
    options = serializers.ListField(child=serializers.CharField(max_length=100), required=False)
    required = serializers.BooleanField(default=False)  # type: ignore[assignment]


class CustomFieldUpdateSerializer(serializers.Serializer[dict[str, Any]]):
    label = serializers.CharField(required=False, max_length=100)  # type: ignore[assignment]
    options = serializers.ListField(child=serializers.CharField(max_length=100), required=False)
    required = serializers.BooleanField(required=False)  # type: ignore[assignment]
    is_active = serializers.BooleanField(required=False)


class CustomFieldReadSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    key = serializers.CharField(read_only=True)
    label = serializers.CharField(read_only=True)  # type: ignore[assignment]  # DRF Field.label
    field_type = serializers.CharField(read_only=True)
    options = serializers.ListField(child=serializers.CharField(), read_only=True)
    required = serializers.BooleanField(read_only=True)  # type: ignore[assignment]
    is_active = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class CustomFieldListSerializer(serializers.Serializer[dict[str, Any]]):
    count = serializers.IntegerField(read_only=True)
    results = CustomFieldReadSerializer(many=True, read_only=True)


def custom_field_to_dict(definition: CustomFieldDefinition) -> dict[str, Any]:
    return {
        "id": str(definition.id),
        "key": definition.key,
        "label": definition.label,
        "field_type": definition.field_type.value,
        "options": list(definition.options),
        "required": definition.required,
        "is_active": definition.is_active,
        "created_at": definition.created_at.isoformat(),
    }
