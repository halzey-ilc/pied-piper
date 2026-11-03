"""Notification tasks (``notifications`` queue).

- ``notifications.task_deadlines`` creates task reminders, overdue alerts and escalations
  for every active company. Idempotent: alerts are keyed, so repeated runs add nothing.
- ``notifications.daily_reports`` sends the automatic morning/evening reports and the
  inactivity alerts (in-app) of every active company whose local report time has come.
  Idempotent: a per-company run marker plus keyed notifications (one per user per day).
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from celery import shared_task

from modules.notifications.composition import daily_report_service, deadline_alert_service
from modules.notifications.infrastructure.daily_reports import report_schedule
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


@shared_task(base=ResilientTask, name="notifications.daily_reports", ignore_result=True)
def daily_reports() -> dict[str, int]:
    totals = {"tenants": 0, "morning": 0, "evening": 0, "inactivity": 0}
    for locator in tenant_directory().list_routable():
        try:
            with system_tenant_scope(locator.slug):
                tenant_id = require_tenant().tenant_id
                zone = ZoneInfo(str(company_settings(tenant_id)["timezone"]))
                result = daily_report_service().run(datetime.now(zone), report_schedule(tenant_id))
        except Exception:
            logger.exception("daily_reports_failed", extra={"tenant_slug": locator.slug})
            continue
        totals["tenants"] += 1
        totals["morning"] += result.morning
        totals["evening"] += result.evening
        totals["inactivity"] += result.inactivity
    return totals
