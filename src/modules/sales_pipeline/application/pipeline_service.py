"""Pipeline use cases (tenant-wide configuration). RBAC enforced here."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.sales_pipeline.application.ports import PipelineRepository
from modules.sales_pipeline.domain.pipeline import Pipeline, StageDefinition
from shared.application.ports import UnitOfWork
from shared.domain.errors import NotFoundError


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
        policy: AccessPolicy | None = None,
    ) -> None:
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
