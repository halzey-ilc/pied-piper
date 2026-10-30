from __future__ import annotations

from uuid import UUID

from django.db import transaction
from django.db.models import F, Q, QuerySet

from modules.sales_pipeline.application.ports import DealFilter, DealVisibility
from modules.sales_pipeline.domain.custom_field import CustomFieldDefinition, CustomFieldType
from modules.sales_pipeline.domain.deal import Deal, DealStatus
from modules.sales_pipeline.domain.loss_reason import LossReason
from modules.sales_pipeline.domain.pipeline import Pipeline, Stage, StageKind
from modules.sales_pipeline.domain.stage_change import StageChange
from modules.sales_pipeline.infrastructure.models import (
    CustomFieldDefinitionRecord,
    DealRecord,
    DealStageChangeRecord,
    LossReasonRecord,
    PipelineRecord,
    StageRecord,
)
from shared.domain.errors import ConflictError
from shared.domain.value_objects import Money


def _pipeline_to_domain(row: PipelineRecord) -> Pipeline:
    stages = tuple(
        Stage(
            id=s.id,
            name=s.name,
            position=s.position,
            probability=s.probability,
            kind=StageKind(s.kind),
            is_active=s.is_active,
        )
        for s in row.stages.all()
    )
    return Pipeline(
        id=row.id,
        name=row.name,
        is_default=row.is_default,
        stages=stages,
        created_at=row.created_at,
    )


def _deal_to_domain(row: DealRecord) -> Deal:
    return Deal(
        id=row.id,
        title=row.title,
        pipeline_id=row.pipeline_id,
        stage_id=row.stage_id,
        value=Money(row.amount, row.currency),
        owner_user_id=row.owner_user_id,
        status=DealStatus(row.status),
        customer_id=row.customer_id,
        team_id=row.team_id,
        source=row.source,
        next_step=row.next_step,
        custom_fields=dict(row.custom_fields or {}),
        expected_close_date=row.expected_close_date,
        closed_at=row.closed_at,
        lost_reason=row.lost_reason,
        loss_reason_id=row.loss_reason_id,
        created_at=row.created_at,
        version=row.version,
    )


def _loss_reason_to_domain(row: LossReasonRecord) -> LossReason:
    return LossReason(id=row.id, name=row.name, is_active=row.is_active, created_at=row.created_at)


class DjangoPipelineRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def add(self, pipeline: Pipeline) -> None:
        with transaction.atomic(using=self.using):
            PipelineRecord.objects.using(self.using).create(
                id=pipeline.id,
                name=pipeline.name,
                is_default=pipeline.is_default,
                created_at=pipeline.created_at,
            )
            StageRecord.objects.using(self.using).bulk_create(
                StageRecord(
                    id=stage.id,
                    pipeline_id=pipeline.id,
                    name=stage.name,
                    position=stage.position,
                    probability=stage.probability,
                    kind=stage.kind.value,
                    is_active=stage.is_active,
                )
                for stage in pipeline.stages
            )

    def get(self, pipeline_id: UUID) -> Pipeline | None:
        row = (
            PipelineRecord.objects.using(self.using)
            .filter(id=pipeline_id)
            .prefetch_related("stages")
            .first()
        )
        return None if row is None else _pipeline_to_domain(row)

    def list_all(self) -> list[Pipeline]:
        rows = (
            PipelineRecord.objects.using(self.using)
            .prefetch_related("stages")
            .order_by("-is_default", "name")
        )
        return [_pipeline_to_domain(row) for row in rows]

    def get_default(self) -> Pipeline | None:
        row = (
            PipelineRecord.objects.using(self.using)
            .filter(is_default=True)
            .prefetch_related("stages")
            .first()
        )
        return None if row is None else _pipeline_to_domain(row)

    def clear_default_flag(self) -> None:
        PipelineRecord.objects.using(self.using).filter(is_default=True).update(is_default=False)


class DjangoDealRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def _qs(self) -> QuerySet[DealRecord]:
        return DealRecord.objects.using(self.using)

    def _visible(self, visibility: DealVisibility, filters: DealFilter) -> QuerySet[DealRecord]:
        qs = self._qs()
        if visibility.owner_user_id is not None:
            condition = Q(owner_user_id=visibility.owner_user_id)
            if visibility.team_ids:
                condition |= Q(team_id__in=visibility.team_ids)
            qs = qs.filter(condition)
        if filters.status is not None:
            qs = qs.filter(status=filters.status.value)
        if filters.pipeline_id is not None:
            qs = qs.filter(pipeline_id=filters.pipeline_id)
        if filters.stage_id is not None:
            qs = qs.filter(stage_id=filters.stage_id)
        return qs

    def add(self, deal: Deal) -> None:
        self._qs().create(
            id=deal.id,
            title=deal.title,
            pipeline_id=deal.pipeline_id,
            stage_id=deal.stage_id,
            status=deal.status.value,
            amount=deal.value.amount,
            currency=deal.value.currency,
            customer_id=deal.customer_id,
            owner_user_id=deal.owner_user_id,
            team_id=deal.team_id,
            source=deal.source,
            next_step=deal.next_step,
            custom_fields=deal.custom_fields,
            expected_close_date=deal.expected_close_date,
            closed_at=deal.closed_at,
            lost_reason=deal.lost_reason,
            loss_reason_id=deal.loss_reason_id,
            version=deal.version,
            created_at=deal.created_at,
        )

    def get(self, deal_id: UUID) -> Deal | None:
        row = self._qs().filter(id=deal_id).first()
        return None if row is None else _deal_to_domain(row)

    def save(self, deal: Deal, expected_version: int) -> None:
        updated = (
            self._qs()
            .filter(id=deal.id, version=expected_version)
            .update(
                title=deal.title,
                stage_id=deal.stage_id,
                status=deal.status.value,
                amount=deal.value.amount,
                currency=deal.value.currency,
                customer_id=deal.customer_id,
                source=deal.source,
                next_step=deal.next_step,
                custom_fields=deal.custom_fields,
                expected_close_date=deal.expected_close_date,
                closed_at=deal.closed_at,
                lost_reason=deal.lost_reason,
                loss_reason_id=deal.loss_reason_id,
                version=F("version") + 1,
            )
        )
        if updated != 1:
            raise ConflictError("Deal was modified by someone else; reload and retry.")

    def list_visible(
        self, visibility: DealVisibility, filters: DealFilter, *, limit: int, offset: int
    ) -> list[Deal]:
        rows = self._visible(visibility, filters).order_by("-created_at", "id")[
            offset : offset + limit
        ]
        return [_deal_to_domain(row) for row in rows]

    def count(self, visibility: DealVisibility, filters: DealFilter) -> int:
        return self._visible(visibility, filters).count()

    def has_open_deals_in_pipeline(self, pipeline_id: UUID) -> bool:
        return self._qs().filter(pipeline_id=pipeline_id, status=DealStatus.OPEN.value).exists()


class DjangoLossReasonRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def add(self, reason: LossReason) -> None:
        LossReasonRecord.objects.using(self.using).create(
            id=reason.id,
            name=reason.name,
            is_active=reason.is_active,
            created_at=reason.created_at,
        )

    def get(self, reason_id: UUID) -> LossReason | None:
        row = LossReasonRecord.objects.using(self.using).filter(id=reason_id).first()
        return None if row is None else _loss_reason_to_domain(row)

    def list_all(self, *, active_only: bool = False) -> list[LossReason]:
        qs = LossReasonRecord.objects.using(self.using)
        if active_only:
            qs = qs.filter(is_active=True)
        return [_loss_reason_to_domain(row) for row in qs.order_by("name")]

    def save(self, reason: LossReason) -> None:
        LossReasonRecord.objects.using(self.using).filter(id=reason.id).update(
            name=reason.name, is_active=reason.is_active
        )

    def exists_by_name(self, name: str) -> bool:
        return LossReasonRecord.objects.using(self.using).filter(name=name).exists()


def _stage_change_to_domain(row: DealStageChangeRecord) -> StageChange:
    return StageChange(
        id=row.id,
        deal_id=row.deal_id,
        from_stage_id=row.from_stage_id,
        to_stage_id=row.to_stage_id,
        author_user_id=row.author_user_id,
        deal_version=row.deal_version,
        reason=row.reason,
        loss_reason_id=row.loss_reason_id,
        created_at=row.created_at,
    )


class DjangoStageChangeRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def add(self, change: StageChange) -> None:
        DealStageChangeRecord.objects.using(self.using).create(
            id=change.id,
            deal_id=change.deal_id,
            from_stage_id=change.from_stage_id,
            to_stage_id=change.to_stage_id,
            author_user_id=change.author_user_id,
            deal_version=change.deal_version,
            reason=change.reason,
            loss_reason_id=change.loss_reason_id,
            created_at=change.created_at,
        )

    def list_for_deal(self, deal_id: UUID) -> list[StageChange]:
        rows = (
            DealStageChangeRecord.objects.using(self.using)
            .filter(deal_id=deal_id)
            .order_by("created_at", "id")
        )
        return [_stage_change_to_domain(row) for row in rows]


def _custom_field_to_domain(row: CustomFieldDefinitionRecord) -> CustomFieldDefinition:
    return CustomFieldDefinition(
        id=row.id,
        key=row.key,
        label=row.label,
        field_type=CustomFieldType(row.field_type),
        options=tuple(row.options or ()),
        required=row.required,
        is_active=row.is_active,
        created_at=row.created_at,
    )


class DjangoCustomFieldRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def add(self, definition: CustomFieldDefinition) -> None:
        CustomFieldDefinitionRecord.objects.using(self.using).create(
            id=definition.id,
            key=definition.key,
            label=definition.label,
            field_type=definition.field_type.value,
            options=list(definition.options),
            required=definition.required,
            is_active=definition.is_active,
            created_at=definition.created_at,
        )

    def get(self, definition_id: UUID) -> CustomFieldDefinition | None:
        row = CustomFieldDefinitionRecord.objects.using(self.using).filter(id=definition_id).first()
        return None if row is None else _custom_field_to_domain(row)

    def list_all(self, *, active_only: bool = False) -> list[CustomFieldDefinition]:
        qs = CustomFieldDefinitionRecord.objects.using(self.using)
        if active_only:
            qs = qs.filter(is_active=True)
        return [_custom_field_to_domain(row) for row in qs.order_by("created_at")]

    def save(self, definition: CustomFieldDefinition) -> None:
        CustomFieldDefinitionRecord.objects.using(self.using).filter(id=definition.id).update(
            label=definition.label,
            options=list(definition.options),
            required=definition.required,
            is_active=definition.is_active,
        )

    def exists_by_key(self, key: str) -> bool:
        return CustomFieldDefinitionRecord.objects.using(self.using).filter(key=key).exists()
