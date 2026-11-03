"""Wires the reference-list use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.reference_lists.application.service import ReferenceListService
from modules.reference_lists.infrastructure.store import DjangoReferenceListStore
from platform_core.application.context import require_tenant
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def reference_list_service() -> ReferenceListService:
    alias = require_tenant().db_alias
    return ReferenceListService(
        store=DjangoReferenceListStore(alias),
        uow=DjangoUnitOfWork(alias),
        audit=DjangoAuditLog(alias),
    )
