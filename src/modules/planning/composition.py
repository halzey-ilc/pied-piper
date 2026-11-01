"""Wires the planning use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.planning.application.service import PlanningService
from modules.planning.infrastructure.repository import (
    DjangoAbsenceRepository,
    DjangoPlanRepository,
)
from modules.planning.infrastructure.roster import DjangoDepartmentRoster
from platform_core.application.context import require_tenant
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def planning_service() -> PlanningService:
    tenant = require_tenant()
    alias = tenant.db_alias
    return PlanningService(
        plans=DjangoPlanRepository(alias),
        absences=DjangoAbsenceRepository(alias),
        roster=DjangoDepartmentRoster(tenant.tenant_id),
        uow=DjangoUnitOfWork(alias),
        audit=DjangoAuditLog(alias),
    )
