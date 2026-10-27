from __future__ import annotations

from uuid import UUID

from django.db import transaction
from django.db.models import F, Q, QuerySet

from modules.sales_pipeline.application.ports import DealFilter, DealVisibility
from modules.sales_pipeline.domain.deal import Deal, DealStatus
from modules.sales_pipeline.domain.pipeline import Pipeline, Stage
from modules.sales_pipeline.infrastructure.models import DealRecord, PipelineRecord, StageRecord
from shared.domain.errors import ConflictError
from shared.domain.value_objects import Money


def _pipeline_to_domain(row: PipelineRecord) -> Pipeline:
    stages = tuple(
        Stage(id=s.id, name=s.name, position=s.position, probability=s.probability)
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
        expected_close_date=row.expected_close_date,
        closed_at=row.closed_at,
        lost_reason=row.lost_reason,
        created_at=row.created_at,
        version=row.version,
    )


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
            expected_close_date=deal.expected_close_date,
            closed_at=deal.closed_at,
            lost_reason=deal.lost_reason,
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
                expected_close_date=deal.expected_close_date,
                closed_at=deal.closed_at,
                lost_reason=deal.lost_reason,
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
