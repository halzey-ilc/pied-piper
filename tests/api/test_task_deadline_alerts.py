"""Task deadline notifications: reminders, overdue alerts, escalation to the head of sales,
idempotency, preferences and tenant isolation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.notifications.composition import deadline_alert_service
from modules.notifications.infrastructure.models import NotificationRecord
from modules.organizations.infrastructure.models import Department, Membership
from platform_core.infrastructure.tenancy.services import system_tenant_scope
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)
TODAY = datetime.now(UTC).date()


def _in_department(user: Any, tenant: TenantFixture) -> None:
    department, _ = Department.objects.get_or_create(organization=tenant.org, name="North")
    Membership.objects.filter(user=user, organization=tenant.org).update(department=department)


def _task(client: APIClient, assignee: Any, due: date, title: str) -> dict[str, Any]:
    customer = client.post("/api/v1/customers/", {"name": f"Client {title}"}, format="json")
    assert customer.status_code == 201, customer.content
    response = client.post(
        "/api/v1/activities/",
        {
            "kind": "task",
            "subject_type": "customer",
            "subject_id": customer.json()["id"],
            "title": title,
            "due_date": due.isoformat(),
            "assignee_user_id": str(assignee.pk),
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    data: dict[str, Any] = response.json()
    return data


def _titles(user: Any) -> list[str]:
    return sorted(
        NotificationRecord.objects.using("tenant_acme")
        .filter(recipient_user_id=user.pk, category="task")
        .values_list("title", flat=True)
    )


def test_reminders_overdue_and_escalation_are_created_once(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    acme = tenants["acme"]
    owner = make_user("owner", tenant=acme)
    head = make_user("head", tenant=acme, role=Role.HEAD_OF_SALES)
    rep = make_user("rep", tenant=acme, role=Role.SALES_REP)
    for user in (head, rep):
        _in_department(user, acme)
    client = client_for(owner, "acme")
    _task(client, rep, TODAY + timedelta(days=1), "Tomorrow")
    _task(client, rep, TODAY + timedelta(days=5), "Later")
    _task(client, rep, TODAY - timedelta(days=1), "Yesterday")
    _task(client, rep, TODAY - timedelta(days=3), "Old")

    with system_tenant_scope("acme"):
        first = deadline_alert_service().run(TODAY)
        again = deadline_alert_service().run(TODAY)

    assert (first.reminders, first.overdue, first.escalations) == (1, 2, 1)
    assert (again.reminders, again.overdue, again.escalations) == (0, 0, 0)
    assert _titles(rep) == [
        "Задача просрочена: Old",
        "Задача просрочена: Yesterday",
        "Срок задачи завтра: Tomorrow",
    ]
    assert _titles(head) == ["Задача сотрудника просрочена на 3 дн.: Old"]
    link = NotificationRecord.objects.using("tenant_acme").filter(recipient_user_id=rep.pk).first()
    assert (
        link is not None and link.link is not None and link.link.startswith("/client-base?client=")
    )
    # Another company's database is untouched.
    assert NotificationRecord.objects.using("tenant_globex").count() == 0


def test_muted_category_and_reminder_lead_time(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    acme = tenants["acme"]
    owner = make_user("owner", tenant=acme)
    rep = make_user("rep", tenant=acme, role=Role.SALES_REP)
    _in_department(rep, acme)
    _task(client_for(owner, "acme"), rep, TODAY + timedelta(days=3), "In three days")
    rep_client = client_for(rep, "acme")

    prefs = rep_client.get("/api/v1/notifications/preferences/").json()
    assert prefs["reminder_days_before"] == 1 and prefs["categories"]["task"] is True
    set_days = rep_client.post(
        "/api/v1/notifications/preferences/", {"reminder_days_before": 3}, format="json"
    )
    assert set_days.status_code == 200 and set_days.json()["reminder_days_before"] == 3
    for bad in ({"reminder_days_before": 9}, {}, {"category": "task"}):
        assert (
            rep_client.post("/api/v1/notifications/preferences/", bad, format="json").status_code
            == 400
        )

    with system_tenant_scope("acme"):
        assert deadline_alert_service().run(TODAY).reminders == 1
    assert _titles(rep) == ["Срок задачи через 3 дн.: In three days"]

    muted = rep_client.post(
        "/api/v1/notifications/preferences/", {"category": "task", "enabled": False}, format="json"
    )
    assert muted.json()["categories"]["task"] is False
    _task(client_for(owner, "acme"), rep, TODAY - timedelta(days=1), "Muted")
    with system_tenant_scope("acme"):
        assert deadline_alert_service().run(TODAY).overdue == 0
