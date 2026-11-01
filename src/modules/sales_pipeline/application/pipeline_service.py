"""Pipeline use cases (tenant-wide configuration). RBAC enforced here."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.sales_pipeline.application.ports import (
    DealFilter,
    DealRepository,
    DealVisibility,
    PipelineRepository,
)
from modules.sales_pipeline.domain.deal import DealStatus
from modules.sales_pipeline.domain.pipeline import Pipeline, Stage, StageDefinition
from shared.application.ports import UnitOfWork
from shared.domain.errors import ConflictError, NotFoundError


@dataclass(frozen=True, slots=True)
class CreatePipeline:
    name: str
    stages: list[StageDefinition]
    is_default: bool = False


class PipelineService:
    def __init__(
        self,
        repository: PipelineRepository,
        uow: UnitOfWork,
        audit: AuditLog,
        deals: DealRepository | None = None,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._deals = deals
        self._repo = repository
        self._uow = uow
        self._audit = audit
        self._policy = policy or AccessPolicy()

    def create(self, principal: Principal, command: CreatePipeline) -> Pipeline:
        self._policy.require(principal, Permission.PIPELINES_MANAGE)
        pipeline = Pipeline.create(
            name=command.name, stages=command.stages, is_default=command.is_default
        )
        with self._uow:
            if pipeline.is_default:
                self._repo.clear_default_flag()
            self._repo.add(pipeline)
            self._audit.record(
                AuditEntry(
                    actor_user_id=principal.user_id,
                    action="pipeline.created",
                    resource_type="pipeline",
                    resource_id=str(pipeline.id),
                )
            )
        return pipeline

    def get(self, principal: Principal, pipeline_id: UUID) -> Pipeline:
        self._policy.require(principal, Permission.PIPELINES_VIEW)
        pipeline = self._repo.get(pipeline_id)
        if pipeline is None:
            raise NotFoundError("Pipeline not found.")
        return pipeline

    def list_pipelines(self, principal: Principal) -> list[Pipeline]:
        self._policy.require(principal, Permission.PIPELINES_VIEW)
        return self._repo.list_all()

    # --- configuration ----------------------------------------------------------
    # Pipelines belong to the company; changing them needs pipelines.manage.

    def update(
        self,
        principal: Principal,
        pipeline_id: UUID,
        *,
        name: str | None = None,
        make_default: bool = False,
    ) -> Pipeline:
        """Rename a pipeline or make it the default one (the default flag moves, never clears)."""
        return self._configure(
            principal,
            pipeline_id,
            lambda p: self._rename_or_default(p, name, make_default),
            "pipeline.updated",
        )

    def add_stage(
        self,
        principal: Principal,
        pipeline_id: UUID,
        definition: StageDefinition,
        position: int | None = None,
    ) -> Pipeline:
        return self._configure(
            principal,
            pipeline_id,
            lambda p: p.add_stage(definition, position) is not None,
            "pipeline.stage_added",
        )

    def update_stage(
        self,
        principal: Principal,
        pipeline_id: UUID,
        stage_id: UUID,
        *,
        name: str | None = None,
        probability: int | None = None,
        is_active: bool | None = None,
    ) -> Pipeline:
        """Rename / re-weight a stage, or archive / restore it.

        A stage holding open deals cannot be archived: move them first, so no open deal
        is stranded on a stage nobody can move it to. Closed deals keep their stage.
        """

        def change(pipeline: Pipeline) -> bool:
            changed = pipeline.update_stage(stage_id, name=name, probability=probability)
            if is_active is not None:
                if not is_active and self._open_deals_on(pipeline.stage(stage_id)):
                    raise StageInUseError("Move the open deals off this stage before archiving it.")
                changed = pipeline.set_stage_active(stage_id, is_active) or changed
            return changed

        return self._configure(principal, pipeline_id, change, "pipeline.stage_updated")

    def reorder_stages(
        self, principal: Principal, pipeline_id: UUID, stage_ids: list[UUID]
    ) -> Pipeline:
        return self._configure(
            principal, pipeline_id, lambda p: p.reorder(stage_ids), "pipeline.stages_reordered"
        )

    def _rename_or_default(self, pipeline: Pipeline, name: str | None, make_default: bool) -> bool:
        changed = pipeline.rename(name) if name is not None else False
        if make_default and not pipeline.is_default:
            self._repo.clear_default_flag()
            pipeline.is_default = True
            changed = True
        return changed

    def _open_deals_on(self, stage: Stage) -> int:
        if self._deals is None:
            return 0
        return self._deals.count(
            DealVisibility(), DealFilter(stage_id=stage.id, status=DealStatus.OPEN)
        )

    def _configure(
        self,
        principal: Principal,
        pipeline_id: UUID,
        change: Callable[[Pipeline], bool],
        action: str,
    ) -> Pipeline:
        self._policy.require(principal, Permission.PIPELINES_MANAGE)
        with self._uow:
            pipeline = self._repo.get(pipeline_id)
            if pipeline is None:
                raise NotFoundError("Pipeline not found.")
            if change(pipeline):
                self._repo.save(pipeline)
                self._audit.record(
                    AuditEntry(
                        actor_user_id=principal.user_id,
                        action=action,
                        resource_type="pipeline",
                        resource_id=str(pipeline.id),
                    )
                )
        return pipeline


class StageInUseError(ConflictError):
    code = "stage_in_use"
