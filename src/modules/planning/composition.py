"""Wires the planning use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.organizations.composition import company_settings
from modules.planning.application.service import PlanningService
from modules.planning.application.work_sessions import WorkSessionService
from modules.planning.infrastructure.actuals import DjangoPlanActuals
from modules.planning.infrastructure.repository import (
    DjangoAbsenceRepository,
    DjangoPlanRepository,
)
from modules.planning.infrastructure.roster import DjangoDepartmentRoster
from modules.planning.infrastructure.work_sessions import DjangoWorkSessionRepository
from platform_core.application.context import require_tenant
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def planning_service() -> PlanningService:
    tenant = require_tenant()
    alias = tenant.db_alias
    settings = company_settings(tenant.tenant_id)
    return PlanningService(
        plans=DjangoPlanRepository(alias),
        absences=DjangoAbsenceRepository(alias),
        roster=DjangoDepartmentRoster(tenant.tenant_id),
        uow=DjangoUnitOfWork(alias),
        audit=DjangoAuditLog(alias),
        actuals=DjangoPlanActuals(alias),
        currency=settings["currency"],
        timezone=settings["timezone"],
    )


def work_session_service() -> WorkSessionService:
    tenant = require_tenant()
    return WorkSessionService(
        repository=DjangoWorkSessionRepository(tenant.db_alias),
        roster=DjangoDepartmentRoster(tenant.tenant_id),
        uow=DjangoUnitOfWork(tenant.db_alias),
    )
