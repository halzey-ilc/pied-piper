"""Wires the risk use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.risk.application.service import RiskService
from modules.risk.infrastructure.reader import DjangoRiskReader
from modules.risk.infrastructure.settings_store import DjangoRiskSettingsStore
from platform_core.application.context import require_tenant
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def risk_service() -> RiskService:
    alias = require_tenant().db_alias
    return RiskService(
        reader=DjangoRiskReader(alias),
        settings=DjangoRiskSettingsStore(alias),
        uow=DjangoUnitOfWork(alias),
        audit=DjangoAuditLog(alias),
    )
