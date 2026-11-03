"""Workspace layout use cases.

Who configures: the director (staff.manage with company scope) — the company layout and any
department's; the head of sales (team scope) — only their own department's. Everyone in the
company reads the layout that applies to them: their department's, else the company's, else
the default. The permission reused is staff.manage: it is exactly "manages the team".
"""

from __future__ import annotations

from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal, Scope
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.workspace.application.ports import DepartmentDirectory, LayoutStore, StoredLayout
from modules.workspace.domain.layout import Layout
from shared.application.ports import UnitOfWork
from shared.domain.errors import AccessDeniedError, NotFoundError


class WorkspaceService:
    def __init__(
        self,
        store: LayoutStore,
        departments: DepartmentDirectory,
        uow: UnitOfWork,
        audit: AuditLog,
        *,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._store = store
        self._departments = departments
        self._uow = uow
        self._audit = audit
        self._policy = policy or AccessPolicy()

    def effective(self, principal: Principal) -> StoredLayout:
        """The layout that applies to the caller (any member of the company)."""
        if principal.department_id is not None:
            own = self._store.load(principal.department_id)
            if own is not None:
                return own
        company = self._store.load(None)
        if company is not None:
            return company
        return StoredLayout(
            layout=Layout.default(), department_id=None, updated_by=None, updated_at=None
        )

    def get(self, principal: Principal, department_id: UUID | None) -> StoredLayout:
        """The configured layout of a scope, falling back like `effective` for that scope."""
        self._check_scope(principal, department_id)
        if department_id is not None:
            own = self._store.load(department_id)
            if own is not None:
                return own
        company = self._store.load(None)
        if company is not None and department_id is None:
            return company
        fallback = company.layout if company is not None else Layout.default()
        return StoredLayout(
            layout=fallback, department_id=department_id, updated_by=None, updated_at=None
        )

    def save(
        self, principal: Principal, department_id: UUID | None, layout: Layout
    ) -> StoredLayout:
        self._check_scope(principal, department_id)
        with self._uow:
            saved = self._store.save(department_id, layout, updated_by=principal.user_id)
            self._audit.record(
                AuditEntry(
                    actor_user_id=principal.user_id,
                    action="workspace_layout.updated",
                    resource_type="workspace_layout",
                    resource_id=str(department_id) if department_id else "company",
                    changed_fields=("blocks",),
                )
            )
        return saved

    def _check_scope(self, principal: Principal, department_id: UUID | None) -> None:
        scope = self._policy.require(principal, Permission.STAFF_MANAGE)
        if scope is Scope.ALL:
            if department_id is not None and not self._departments.exists(
                principal.tenant_id, department_id
            ):
                raise NotFoundError("Department not found.")
            return
        # A head of sales configures only their own department.
        if department_id is None or department_id != principal.department_id:
            raise AccessDeniedError("You can configure the workspace of your own department only.")
