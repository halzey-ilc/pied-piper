"""Message templates of the company: managers read them, heads of sales and the director
maintain them. Templates only fill the text; sending still needs a connected channel."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.communications.domain.template import MessageTemplate
from shared.application.ports import UnitOfWork
from shared.domain.errors import NotFoundError

AGGREGATE = "message_template"


class TemplateRepository(Protocol):
    def list_all(self) -> list[MessageTemplate]: ...

    def get(self, template_id: UUID) -> MessageTemplate | None: ...

    def add(self, template: MessageTemplate) -> None: ...

    def save(self, template: MessageTemplate) -> None: ...

    def delete(self, template_id: UUID) -> None: ...


class TemplateService:
    def __init__(
        self,
        repository: TemplateRepository,
        uow: UnitOfWork,
        audit: AuditLog,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._repo = repository
        self._uow = uow
        self._audit = audit
        self._policy = policy or AccessPolicy()

    def list(self, principal: Principal) -> list[MessageTemplate]:
        self._policy.require(principal, Permission.COMMUNICATIONS_VIEW)
        return self._repo.list_all()

    def create(self, principal: Principal, *, title: str, body: str) -> MessageTemplate:
        # Managing shared texts is a manager-of-people right (head of sales, director).
        self._policy.require(principal, Permission.STAFF_MANAGE)
        template = MessageTemplate.create(title=title, body=body, created_by=principal.user_id)
        with self._uow:
            self._repo.add(template)
            self._record(principal, "message_template.created", template.id)
        return template

    def update(
        self, principal: Principal, template_id: UUID, *, title: str | None, body: str | None
    ) -> MessageTemplate:
        self._policy.require(principal, Permission.STAFF_MANAGE)
        with self._uow:
            template = self._existing(template_id)
            template.edit(title=title, body=body)
            self._repo.save(template)
            self._record(principal, "message_template.updated", template.id)
        return template

    def delete(self, principal: Principal, template_id: UUID) -> None:
        self._policy.require(principal, Permission.STAFF_MANAGE)
        with self._uow:
            self._existing(template_id)
            self._repo.delete(template_id)
            self._record(principal, "message_template.deleted", template_id)

    def _existing(self, template_id: UUID) -> MessageTemplate:
        template = self._repo.get(template_id)
        if template is None:
            raise NotFoundError("Template not found.")
        return template

    def _record(self, principal: Principal, action: str, template_id: UUID) -> None:
        self._audit.record(
            AuditEntry(
                actor_user_id=principal.user_id,
                action=action,
                resource_type=AGGREGATE,
                resource_id=str(template_id),
            )
        )
