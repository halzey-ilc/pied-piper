"""Wires the notification use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from modules.notifications.application.daily_reports import DailyReportService
from modules.notifications.application.deadlines import DeadlineAlertService
from modules.notifications.application.service import NotificationService
from modules.notifications.infrastructure.daily_reports import (
    DjangoDailyFigures,
    DjangoReportRoster,
    DjangoReportRuns,
    company_currency,
)
from modules.notifications.infrastructure.deadlines import (
    DjangoDepartmentHeads,
    DjangoTaskDeadlineSource,
)
from modules.notifications.infrastructure.repository import (
    DjangoNotificationRepository,
    DjangoPreferenceRepository,
)
from platform_core.application.context import require_tenant
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def notification_service() -> NotificationService:
    alias = require_tenant().db_alias
    return NotificationService(
        notifications=DjangoNotificationRepository(alias),
        preferences=DjangoPreferenceRepository(alias),
        uow=DjangoUnitOfWork(alias),
    )


def deadline_alert_service() -> DeadlineAlertService:
    """Task deadline reminders/escalations for the company bound to the current scope."""
    tenant = require_tenant()
    return DeadlineAlertService(
        notifications=notification_service(),
        preferences=DjangoPreferenceRepository(tenant.db_alias),
        tasks=DjangoTaskDeadlineSource(tenant.db_alias),
        heads=DjangoDepartmentHeads(tenant.tenant_id),
    )


def daily_report_service() -> DailyReportService:
    """Morning/evening reports and inactivity alerts for the company bound to the scope."""
    tenant = require_tenant()
    # Money in the reports is summed in the company currency only (confirmed payments).
    currency = company_currency(tenant.tenant_id)
    return DailyReportService(
        notifications=notification_service(),
        roster=DjangoReportRoster(tenant.tenant_id),
        figures=DjangoDailyFigures(tenant.db_alias, currency),
        runs=DjangoReportRuns(tenant.db_alias),
        currency=currency,
    )
