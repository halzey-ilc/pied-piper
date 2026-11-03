"""Wires the notification use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from modules.notifications.application.deadlines import DeadlineAlertService
from modules.notifications.application.service import NotificationService
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
