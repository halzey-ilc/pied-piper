"""Loss reason catalog use cases (tenant-wide configuration). RBAC enforced here."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.sales_pipeline.application.ports import LossReasonRepository
from modules.sales_pipeline.domain.loss_reason import LossReason
from shared.application.ports import UnitOfWork
from shared.domain.errors import ConflictError, NotFoundError


@dataclass(frozen=True, slots=True)
class UpdateLossReason:
    reason_id: UUID
    name: str | None = None
    is_active: bool | None = None


class LossReasonService:
    def __init__(
        self,
        repository: LossReasonRepository,
        uow: UnitOfWork,
        audit: AuditLog,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._repo = repository
        self._uow = uow
        self._audit = audit
        self._policy = policy or AccessPolicy()

    def list_reasons(self, principal: Principal, *, active_only: bool = False) -> list[LossReason]:
        self._policy.require(principal, Permission.PIPELINES_VIEW)
        return self._repo.list_all(active_only=active_only)

    def create(self, principal: Principal, name: str) -> LossReason:
        self._policy.require(principal, Permission.PIPELINES_MANAGE)
        reason = LossReason.create(name=name)
        with self._uow:
            if self._repo.exists_by_name(reason.name):
                raise ConflictError("A loss reason with that name already exists.")
            self._repo.add(reason)
            self._record(principal, "loss_reason.created", reason)
        return reason

    def update(self, principal: Principal, command: UpdateLossReason) -> LossReason:
        self._policy.require(principal, Permission.PIPELINES_MANAGE)
        with self._uow:
            reason = self._repo.get(command.reason_id)
            if reason is None:
                raise NotFoundError("Loss reason not found.")
            changed = False
            if command.name is not None and reason.name != command.name.strip():
                if self._repo.exists_by_name(" ".join(command.name.split())):
                    raise ConflictError("A loss reason with that name already exists.")
                changed = reason.rename(command.name) or changed
            if command.is_active is not None:
                changed = reason.set_active(is_active=command.is_active) or changed
            if changed:
                self._repo.save(reason)
                self._record(principal, "loss_reason.updated", reason)
        return reason

    def _record(self, principal: Principal, action: str, reason: LossReason) -> None:
        self._audit.record(
            AuditEntry(
                actor_user_id=principal.user_id,
                action=action,
                resource_type="loss_reason",
                resource_id=str(reason.id),
            )
        )
