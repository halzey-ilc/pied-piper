"""Wires the report use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from django.conf import settings

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.reporting.application.service import ReportService
from modules.reporting.infrastructure.renderer import KpiCsvRenderer
from modules.reporting.infrastructure.repository import DjangoReportJobRepository
from modules.reporting.infrastructure.scheduler import CeleryReportScheduler
from modules.reporting.infrastructure.storage import FilesystemExportStorage
from platform_core.application.context import require_tenant
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def report_service() -> ReportService:
    tenant = require_tenant()
    alias = tenant.db_alias
    return ReportService(
        jobs=DjangoReportJobRepository(alias),
        renderer=KpiCsvRenderer(alias),
        storage=FilesystemExportStorage(settings.REPORTS["STORAGE_DIR"]),
        scheduler=CeleryReportScheduler(alias),
        uow=DjangoUnitOfWork(alias),
        audit=DjangoAuditLog(alias),
        tenant_slug=tenant.slug,
    )
