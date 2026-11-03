"""Wires the workspace use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.workspace.application.service import WorkspaceService
from modules.workspace.infrastructure.store import DjangoDepartmentDirectory, DjangoLayoutStore
from platform_core.application.context import require_tenant
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def workspace_service() -> WorkspaceService:
    alias = require_tenant().db_alias
    return WorkspaceService(
        store=DjangoLayoutStore(alias),
        departments=DjangoDepartmentDirectory(),
        uow=DjangoUnitOfWork(alias),
        audit=DjangoAuditLog(alias),
    )
