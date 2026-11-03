"""Notification tasks (``notifications`` queue).

- ``notifications.task_deadlines`` creates task reminders, overdue alerts and escalations
  for every active company. Idempotent: alerts are keyed, so repeated runs add nothing.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from celery import shared_task

from modules.notifications.composition import deadline_alert_service
from modules.organizations.composition import company_settings
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.tasks import ResilientTask
from platform_core.infrastructure.tenancy.services import system_tenant_scope, tenant_directory

logger = logging.getLogger(__name__)


@shared_task(base=ResilientTask, name="notifications.task_deadlines", ignore_result=True)
def task_deadlines() -> dict[str, int]:
    totals = {"tenants": 0, "reminders": 0, "overdue": 0, "escalations": 0}
    for locator in tenant_directory().list_routable():
        try:
            with system_tenant_scope(locator.slug):
                zone = ZoneInfo(str(company_settings(require_tenant().tenant_id)["timezone"]))
                result = deadline_alert_service().run(datetime.now(zone).date())
        except Exception:
            logger.exception("task_deadlines_failed", extra={"tenant_slug": locator.slug})
            continue
        totals["tenants"] += 1
        totals["reminders"] += result.reminders
        totals["overdue"] += result.overdue
        totals["escalations"] += result.escalations
    return totals
