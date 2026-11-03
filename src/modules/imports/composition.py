"""Wires the import use case to adapters bound to the active tenant database."""

from __future__ import annotations

from uuid import UUID

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.imports.application.service import ImportService
from modules.imports.infrastructure.store import (
    DjangoDealReferences,
    DjangoExistingContacts,
    DjangoImportJobStore,
    ServiceDealCreator,
    ServiceRecordCreator,
)
from modules.subscriptions.infrastructure.gate import check_room
from platform_core.application.context import require_tenant
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def import_service() -> ImportService:
    tenant = require_tenant()
    alias = tenant.db_alias
    return ImportService(
        contacts=DjangoExistingContacts(alias),
        creator=ServiceRecordCreator(),
        jobs=DjangoImportJobStore(alias),
        audit=DjangoAuditLog(alias),
        uow=DjangoUnitOfWork(alias),
        deal_creator=ServiceDealCreator(alias),
        deal_refs=DjangoDealReferences(alias, tenant.tenant_id),
        capacity=_TenantPlanCapacity(tenant.tenant_id, alias),
    )


class _TenantPlanCapacity:
    def __init__(self, tenant_id: UUID, alias: str) -> None:
        self._tenant_id = tenant_id
        self._alias = alias

    def check_room(self, metric: str) -> None:
        check_room(self._tenant_id, self._alias, metric)
