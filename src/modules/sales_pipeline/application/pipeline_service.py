"""Pipeline use cases (sales funnel configuration). RBAC enforced here.

A pipeline is company-wide (``department_id`` None) or belongs to one sales department.
The director (pipelines.manage with ALL scope) configures any pipeline; a head of sales
(TEAM scope) only their own department's ones; managers never change the structure.
Members of a department see the company-wide pipelines and their department's own;
other departments' pipelines are hidden from them (404).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal, Scope
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.sales_pipeline.application.ports import (
    DealFilter,
    DealRepository,
    DealVisibility,
    PipelineRepository,
    StageChangeRepository,
)
from modules.sales_pipeline.domain.deal import DealStatus
from modules.sales_pipeline.domain.pipeline import Pipeline, Stage, StageDefinition
from modules.sales_pipeline.domain.stage_change import StageChange
from shared.application.ports import UnitOfWork
from shared.domain.errors import (
    AccessDeniedError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)


@dataclass(frozen=True, slots=True)
class CreatePipeline:
    name: str
    stages: list[StageDefinition]
    is_default: bool = False
    # For a director: the department the pipeline belongs to (None = company-wide).
    # A head of sales always creates pipelines of their own department.
    department_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class StageRemoval:
    """Outcome of deleting a stage: deals moved and whether the stage was archived (it had
    a history) or removed outright (no deal ever stood on it)."""

    pipeline: Pipeline
    moved_deals: int
    archived: bool


class PipelineService:
    def __init__(
        self,
        repository: PipelineRepository,
        uow: UnitOfWork,
        audit: AuditLog,
        deals: DealRepository | None = None,
        policy: AccessPolicy | None = None,
        stage_changes: StageChangeRepository | None = None,
    ) -> None:
        self._deals = deals
        self._stage_changes = stage_changes
        self._repo = repository
        self._uow = uow
        self._audit = audit
        self._policy = policy or AccessPolicy()

    def create(self, principal: Principal, command: CreatePipeline) -> Pipeline:
        scope = self._policy.require(principal, Permission.PIPELINES_MANAGE)
        department_id = command.department_id
        if scope is not Scope.ALL:
            if principal.department_id is None:
                raise AccessDeniedError("You are not assigned to a department.")
            department_id = principal.department_id
        pipeline = Pipeline.create(
            name=command.name,
            stages=command.stages,
            is_default=command.is_default,
            department_id=department_id,
        )
        with self._uow:
            # A department's first pipeline becomes its default: its managers land there.
            if department_id is not None and self._repo.get_default(department_id) is None:
                pipeline.is_default = True
            if pipeline.is_default:
                self._repo.clear_default_flag(department_id)
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
        if pipeline is None or not self._visible(principal, pipeline):
            raise NotFoundError("Pipeline not found.")
        return pipeline

    def list_pipelines(self, principal: Principal) -> list[Pipeline]:
        """Visible pipelines; the caller's department pipelines first (its default first)."""
        self._policy.require(principal, Permission.PIPELINES_VIEW)
        visible = [p for p in self._repo.list_all() if self._visible(principal, p)]
        own = principal.department_id
        return sorted(
            visible,
            key=lambda p: (
                0 if own is not None and p.department_id == own else 1,
                not p.is_default,
                p.name.lower(),
            ),
        )

    def stage_counts(self, principal: Principal, pipeline_id: UUID) -> dict[UUID, tuple[int, int]]:
        """Deals per stage (open, all) for the funnel settings; only for whoever may configure."""
        pipeline = self.get(principal, pipeline_id)
        self._require_manager_of(principal, pipeline)
        return self._deals.stage_counts(pipeline.id) if self._deals is not None else {}

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

    def delete_stage(
        self,
        principal: Principal,
        pipeline_id: UUID,
        stage_id: UUID,
        *,
        move_to_stage_id: UUID | None,
    ) -> StageRemoval:
        """Delete a stage. Its deals (any status) move to ``move_to_stage_id`` — an active
        stage of the same kind in the same pipeline, chosen by the user — and each move is
        written to the deal's stage history. A stage that ever held a deal is archived (its
        name stays in history); a never-used one is removed outright."""
        deals = self._deals
        if deals is None:  # pragma: no cover - always wired in composition
            raise ConflictError("Stage deletion is not available.")
        with self._uow:
            pipeline = self._load_for_config(principal, pipeline_id)
            stage = pipeline.stage(stage_id)
            moved: list[tuple[UUID, int]] = []
            if deals.stage_counts(pipeline.id).get(stage.id, (0, 0))[1] > 0:
                if move_to_stage_id is None:
                    raise MoveTargetRequiredError(
                        "The stage has deals: choose the stage to move them to."
                    )
                target = pipeline.stage(move_to_stage_id)
                if target.id == stage.id or not target.is_active or target.kind is not stage.kind:
                    raise DomainValidationError(
                        "Deals can move only to another active stage of the same kind."
                    )
                moved = deals.move_all_on_stage(stage.id, target.id)
                note = f"Этап «{stage.name}» удалён: сделка перенесена на «{target.name}»"
                for deal_id, version in moved:
                    if self._stage_changes is not None:
                        self._stage_changes.add(
                            StageChange.record(
                                deal_id=deal_id,
                                from_stage_id=stage.id,
                                to_stage_id=target.id,
                                author_user_id=principal.user_id,
                                deal_version=version,
                                reason=note,
                            )
                        )
            archived = deals.stage_ever_used(stage.id)
            if archived:
                if stage.is_active:
                    pipeline.set_stage_active(stage.id, False)
            else:
                pipeline.remove_stage(stage.id)
            self._repo.save(pipeline)
            self._audit.record(
                AuditEntry(
                    actor_user_id=principal.user_id,
                    action="pipeline.stage_deleted",
                    resource_type="pipeline",
                    resource_id=str(pipeline.id),
                    changed_fields=("stages",),
                )
            )
        return StageRemoval(pipeline=pipeline, moved_deals=len(moved), archived=archived)

    def reorder_stages(
        self, principal: Principal, pipeline_id: UUID, stage_ids: list[UUID]
    ) -> Pipeline:
        return self._configure(
            principal, pipeline_id, lambda p: p.reorder(stage_ids), "pipeline.stages_reordered"
        )

    def _rename_or_default(self, pipeline: Pipeline, name: str | None, make_default: bool) -> bool:
        changed = pipeline.rename(name) if name is not None else False
        if make_default and not pipeline.is_default:
            self._repo.clear_default_flag(pipeline.department_id)
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
        with self._uow:
            pipeline = self._load_for_config(principal, pipeline_id)
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

    # --- access ---------------------------------------------------------------

    def _visible(self, principal: Principal, pipeline: Pipeline) -> bool:
        if pipeline.department_id is None:
            return True
        if self._policy.scope_for(principal, Permission.PIPELINES_MANAGE) is Scope.ALL:
            return True
        return pipeline.department_id == principal.department_id

    def _require_manager_of(self, principal: Principal, pipeline: Pipeline) -> None:
        scope = self._policy.require(principal, Permission.PIPELINES_MANAGE)
        if scope is Scope.ALL:
            return
        if pipeline.department_id is None or pipeline.department_id != principal.department_id:
            raise AccessDeniedError("You can configure only your department's pipeline.")

    def _load_for_config(self, principal: Principal, pipeline_id: UUID) -> Pipeline:
        self._policy.require(principal, Permission.PIPELINES_MANAGE)
        pipeline = self._repo.get_for_update(pipeline_id)
        if pipeline is None or not self._visible(principal, pipeline):
            raise NotFoundError("Pipeline not found.")
        self._require_manager_of(principal, pipeline)
        return pipeline


class StageInUseError(ConflictError):
    code = "stage_in_use"


class MoveTargetRequiredError(DomainValidationError):
    code = "move_target_required"
