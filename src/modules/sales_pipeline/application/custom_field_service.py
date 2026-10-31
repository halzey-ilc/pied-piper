"""Custom field definition use cases (tenant-wide configuration). RBAC enforced here."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.sales_pipeline.application.ports import CustomFieldRepository
from modules.sales_pipeline.domain.custom_field import CustomFieldDefinition, CustomFieldType
from shared.application.ports import UnitOfWork
from shared.domain.errors import ConflictError, NotFoundError


@dataclass(frozen=True, slots=True)
class CreateCustomField:
    key: str
    label: str
    field_type: CustomFieldType
    options: Sequence[str] = ()
    required: bool = False


@dataclass(frozen=True, slots=True)
class UpdateCustomField:
    definition_id: UUID
    label: str | None = None
    options: Sequence[str] | None = None
    required: bool | None = None
    is_active: bool | None = None


class CustomFieldService:
    def __init__(
        self,
        repository: CustomFieldRepository,
        uow: UnitOfWork,
        audit: AuditLog,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._repo = repository
        self._uow = uow
        self._audit = audit
        self._policy = policy or AccessPolicy()

    def list_fields(
        self, principal: Principal, *, active_only: bool = False
    ) -> list[CustomFieldDefinition]:
        self._policy.require(principal, Permission.PIPELINES_VIEW)
        return self._repo.list_all(active_only=active_only)

    def create(self, principal: Principal, command: CreateCustomField) -> CustomFieldDefinition:
        self._policy.require(principal, Permission.PIPELINES_MANAGE)
        definition = CustomFieldDefinition.create(
            key=command.key,
            label=command.label,
            field_type=command.field_type,
            options=command.options,
            required=command.required,
        )
        with self._uow:
            if self._repo.exists_by_key(definition.key):
                raise ConflictError("A custom field with that key already exists.")
            self._repo.add(definition)
            self._record(principal, "custom_field.created", definition)
        return definition

    def update(self, principal: Principal, command: UpdateCustomField) -> CustomFieldDefinition:
        self._policy.require(principal, Permission.PIPELINES_MANAGE)
        with self._uow:
            definition = self._repo.get(command.definition_id)
            if definition is None:
                raise NotFoundError("Custom field not found.")
            changed = False
            if command.label is not None:
                changed = definition.relabel(command.label) or changed
            if command.options is not None:
                changed = definition.set_options(command.options) or changed
            if command.required is not None:
                changed = definition.set_required(required=command.required) or changed
            if command.is_active is not None:
                changed = definition.set_active(is_active=command.is_active) or changed
            if changed:
                self._repo.save(definition)
                self._record(principal, "custom_field.updated", definition)
        return definition

    def _record(self, principal: Principal, action: str, definition: CustomFieldDefinition) -> None:
        self._audit.record(
            AuditEntry(
                actor_user_id=principal.user_id,
                action=action,
                resource_type="custom_field",
                resource_id=str(definition.id),
            )
        )
