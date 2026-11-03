"""Missed-call follow-up (spec 4.12, 6.9, 6.10): a call that ends as missed gets a call-back
task for its owner and an in-app alert. Works for calls logged through the API and for calls
a provider ends via webhook — both emit ``telephony.call_ended``.

Runs in the event dispatcher on the active tenant database; idempotent per event (the inbox
dedupes redeliveries per consumer). The task needs a customer or deal: the call's own link,
else the one active customer with that phone number. Without one only the alert is created.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from modules.access_control.domain.rbac import Principal, Role
from modules.activities.application.service import LogTask
from modules.activities.composition import activity_service
from modules.activities.domain.activity import SubjectRef, SubjectType, TaskPriority, TaskType
from modules.customers.composition import customer_id_by_phone
from modules.notifications.composition import notification_service
from modules.notifications.domain.notification import NotificationCategory
from modules.organizations.composition import company_settings
from modules.organizations.infrastructure.memberships import DjangoMembershipProvider
from modules.telephony.composition import call_repository
from modules.telephony.domain.call import CallDirection, CallStatus
from platform_core.application.context import require_tenant
from shared.domain.errors import DomainError

CONSUMER = "telephony.missed_call_follow_up"


def _principal(user_id: UUID) -> Principal | None:
    tenant_id = require_tenant().tenant_id
    membership = DjangoMembershipProvider().get_membership(user_id, tenant_id)
    if membership is None:
        return None
    return Principal(
        user_id=user_id,
        tenant_id=tenant_id,
        role=Role(membership.role),
        team_ids=membership.team_ids,
        department_id=membership.department_id,
    )


def on_call_ended(message: dict[str, Any]) -> None:
    payload = message["payload"]
    if payload.get("status") != CallStatus.MISSED.value:
        return
    call = call_repository().get(UUID(payload["call_id"]))
    if call is None:
        return
    number = call.from_number if call.direction is CallDirection.INBOUND else call.to_number
    principal = _principal(call.owner_user_id)
    if principal is None:  # the owner left the company meanwhile
        return
    subject: SubjectRef | None = None
    if call.link is not None:
        subject = SubjectRef(type=SubjectType(call.link.type.value), id=call.link.id)
    else:
        customer = customer_id_by_phone(number.value)
        if customer is not None:
            subject = SubjectRef(type=SubjectType.CUSTOMER, id=customer)
    task_created = False
    if subject is not None:
        zone = ZoneInfo(str(company_settings(principal.tenant_id)["timezone"]))
        try:
            activity_service().log_task(
                principal,
                LogTask(
                    subject=subject,
                    title=f"Перезвонить: пропущенный звонок {number}",
                    due_date=datetime.now(UTC).astimezone(zone).date(),
                    task_type=TaskType.CALL,
                    priority=TaskPriority.HIGH,
                    assignee_user_id=call.owner_user_id,
                ),
            )
            task_created = True
        except DomainError:
            # E.g. the deal already has two open tasks: the alert still goes out.
            task_created = False
    notification_service().notify(
        recipient_user_id=call.owner_user_id,
        category=NotificationCategory.SYSTEM,
        title="Пропущенный звонок",
        body=(
            f"Перезвоните на {number}: задача поставлена на сегодня."
            if task_created
            else f"Перезвоните на {number}. Задача не поставлена: привяжите звонок к клиенту "
            "или сделке, у которой меньше двух открытых задач."  # noqa: RUF001
        ),
        source_event_id=UUID(message["event_id"]),
        link="/calls",
    )


def register_handlers(registry: Any) -> None:
    if CONSUMER not in registry.handlers_for("telephony.call_ended"):
        registry.register("telephony.call_ended", CONSUMER, on_call_ended)
