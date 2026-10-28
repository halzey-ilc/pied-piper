"""Wires the operations use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.operations.application.service import OperationsService
from modules.operations.infrastructure.reader import DjangoOperationsReader
from modules.operations.infrastructure.settings_store import DjangoSettingsStore
from platform_core.application.context import require_tenant
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def operations_service() -> OperationsService:
    alias = require_tenant().db_alias
    return OperationsService(
        reader=DjangoOperationsReader(alias),
        settings=DjangoSettingsStore(alias),
        uow=DjangoUnitOfWork(alias),
        audit=DjangoAuditLog(alias),
    )
