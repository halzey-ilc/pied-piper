"""Reporting / analytics tasks (``reports`` queue).

- ``reports.process_report`` renders one queued report off the request path.
- ``reports.compute_daily_kpis`` snapshots today's KPIs for every active tenant.
Both are thin adapters over application services.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from celery import shared_task

from modules.reporting.composition import report_service
from modules.sales_kpi.composition import snapshot_writer
from modules.sales_kpi.domain.kpi import Period
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.tasks import ResilientTask
from platform_core.infrastructure.tenancy.services import system_tenant_scope, tenant_directory

logger = logging.getLogger(__name__)


@shared_task(base=ResilientTask, name="reports.process_report", ignore_result=True)
def process_report(tenant_slug: str, job_id: str) -> None:
    with system_tenant_scope(tenant_slug):
        report_service().run(UUID(job_id))
    logger.info("report_rendered", extra={"job_id": job_id})


@shared_task(base=ResilientTask, name="reports.compute_daily_kpis", ignore_result=True)
def compute_daily_kpis() -> dict[str, int]:
    today = datetime.now(UTC).date()
    tenants = 0
    for locator in tenant_directory().list_routable():
        try:
            with system_tenant_scope(locator.slug):
                snapshot_writer(require_tenant().db_alias).compute_and_store(Period.DAY, today)
        except Exception:
            logger.exception("kpi_snapshot_failed", extra={"tenant_slug": locator.slug})
            continue
        tenants += 1
    return {"tenants": tenants}
