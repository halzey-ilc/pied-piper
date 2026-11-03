"""Wires the import use case to adapters bound to the active tenant database."""

from __future__ import annotations

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.imports.application.service import ImportService
from modules.imports.infrastructure.store import (
    DjangoExistingContacts,
    DjangoImportJobStore,
    ServiceRecordCreator,
)
from platform_core.application.context import require_tenant
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def import_service() -> ImportService:
    alias = require_tenant().db_alias
    return ImportService(
        contacts=DjangoExistingContacts(alias),
        creator=ServiceRecordCreator(),
        jobs=DjangoImportJobStore(alias),
        audit=DjangoAuditLog(alias),
        uow=DjangoUnitOfWork(alias),
    )
