"""Automatic morning/evening reports and inactivity alerts (in-app): recipients per role,
absent/inactive members excluded, confirmed-only money, idempotency within a local day,
company-local time of day, the company settings and tenant isolation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from entrypoints.workers.tasks import notifications as notification_tasks
from modules.access_control.domain.rbac import Role
from modules.activities.infrastructure.models import ActivityRecord
from modules.notifications.composition import daily_report_service
from modules.notifications.domain.daily_reports import ReportSchedule
from modules.notifications.infrastructure.daily_reports import report_schedule
from modules.notifications.infrastructure.models import DailyReportRunRecord, NotificationRecord
from modules.organizations.infrastructure.models import Department, Membership
from modules.payments.infrastructure.models import PaymentRecord
from modules.planning.infrastructure.models import AbsenceRecord
from platform_core.infrastructure.tenancy.services import system_tenant_scope
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)

ACME = "tenant_acme"
BISHKEK = ZoneInfo("Asia/Bishkek")  # the default company timezone


def _today() -> date:
    return datetime.now(BISHKEK).date()


def _at(hour: int, minute: int = 0, zone: ZoneInfo = BISHKEK) -> datetime:
    return datetime.combine(datetime.now(zone).date(), time(hour, minute), tzinfo=zone)


def _department(user: Any, tenant: TenantFixture, name: str = "North") -> None:
    department, _ = Department.objects.get_or_create(organization=tenant.org, name=name)
    Membership.objects.filter(user=user, organization=tenant.org).update(department=department)


def _team(make_user: Callable[..., Any], acme: TenantFixture) -> dict[str, Any]:
    people = {
        "director": make_user("director", tenant=acme, role=Role.DIRECTOR),
        "head": make_user("head", tenant=acme, role=Role.HEAD_OF_SALES),
        "busy": make_user("busy", tenant=acme, role=Role.SALES_REP),
        "silent": make_user("silent", tenant=acme, role=Role.SALES_REP),
        "away": make_user("away", tenant=acme, role=Role.SALES_REP),
        "dismissed": make_user("dismissed", tenant=acme, role=Role.SALES_REP),
        "disabled": make_user("disabled", tenant=acme, role=Role.SALES_REP),
        "caller": make_user("caller", tenant=acme, role=Role.LEAD_CALLER),
    }
    for key in ("head", "busy", "silent", "away", "dismissed", "disabled", "caller"):
        _department(people[key], acme)
    Membership.objects.filter(user=people["dismissed"], organization=acme.org).update(
        is_active=False
    )
    people["disabled"].is_active = False
    people["disabled"].save(update_fields=["is_active"])
    AbsenceRecord.objects.using(ACME).create(
        id=uuid4(),
        user_id=people["away"].pk,
        kind="vacation",
        start_date=_today() - timedelta(days=1),
        end_date=_today() + timedelta(days=1),
        created_by=people["director"].pk,
        created_at=timezone.now(),
    )
    return people


def _reports(user: Any, category: str = "report") -> list[NotificationRecord]:
    return list(
        NotificationRecord.objects.using(ACME).filter(recipient_user_id=user.pk, category=category)
    )


def _task_due_today(user: Any) -> None:
    ActivityRecord.objects.using(ACME).create(
        id=uuid4(),
        kind="task",
        subject_type="customer",
        subject_id=uuid4(),
        owner_user_id=user.pk,
        assignee_user_id=user.pk,
        title="Call back",
        due_date=_today(),
        task_status="open",
        created_at=timezone.now(),
    )


def _payment(user: Any, amount: str, status: str) -> None:
    PaymentRecord.objects.using(ACME).create(
        id=uuid4(),
        amount=Decimal(amount),
        currency="KGS",
        kind="prepayment",
        paid_on=_today(),
        seller_user_id=user.pk,
        owner_user_id=user.pk,
        created_at=timezone.now(),
        status=status,
    )


def test_morning_report_goes_to_each_role_once(
    make_user: Callable[..., Any], tenants: dict[str, TenantFixture]
) -> None:
    acme = tenants["acme"]
    people = _team(make_user, acme)
    _task_due_today(people["busy"])
    schedule = ReportSchedule()

    with system_tenant_scope("acme"):
        early = daily_report_service().run(_at(8, 55), schedule)
        first = daily_report_service().run(_at(9, 5), schedule)
        again = daily_report_service().run(_at(9, 10), schedule)

    assert (early.morning, early.evening) == (0, 0)
    # busy + silent (managers), head (department), director (company)
    assert first.morning == 4 and first.evening == 0
    assert again.morning == 0
    [busy] = _reports(people["busy"])
    assert busy.title.startswith("Утренний отчёт на") and busy.link == "/my-day"
    assert "Задачи на сегодня: 1" in busy.body
    [head] = _reports(people["head"])
    assert head.title.startswith("Утренний отчёт отдела «North»") and head.link == "/team"
    assert "busy:" in head.body and "silent:" in head.body and "Отсутствуют: away." in head.body
    [director] = _reports(people["director"])
    assert director.title.startswith("Утренний отчёт компании") and "2 менеджера" in director.body
    for key in ("away", "dismissed", "disabled", "caller"):
        assert _reports(people[key]) == [], key
    assert DailyReportRunRecord.objects.using(ACME).filter(kind="morning").count() == 1
    # Another company's database is untouched.
    assert NotificationRecord.objects.using("tenant_globex").count() == 0


def test_evening_report_counts_confirmed_money_only_and_flags_inactivity(
    make_user: Callable[..., Any], tenants: dict[str, TenantFixture]
) -> None:
    acme = tenants["acme"]
    people = _team(make_user, acme)
    _payment(people["busy"], "1000", "confirmed")
    _payment(people["busy"], "5000", "pending_verification")

    with system_tenant_scope("acme"):
        first = daily_report_service().run(_at(19, 5), ReportSchedule())
        # The marker is gone (e.g. a restore): the keyed notifications still say "once".
        DailyReportRunRecord.objects.using(ACME).all().delete()
        again = daily_report_service().run(_at(19, 30), ReportSchedule())

    assert (first.morning, first.evening, first.inactivity) == (0, 4, 1)
    assert (again.evening, again.inactivity) == (0, 0)
    [busy] = _reports(people["busy"])
    assert busy.title.startswith("Вечерний отчёт за")
    assert "внесено 2, подтверждено 1 на 1 000 KGS" in busy.body
    assert "6 000" not in busy.body and "5 000" not in busy.body
    [silent] = _reports(people["silent"])
    assert "активности не зафиксировано" in silent.body
    [head_report] = _reports(people["head"])
    assert head_report.link == "/daily-reports" and "Без активности: silent." in head_report.body
    [alert] = _reports(people["head"], "inactivity")
    assert alert.title.startswith("Нет активности за") and alert.link == "/team"
    assert alert.body.startswith("silent —") and "busy" not in alert.body
    # The department has a head of sales: the director gets the summary, not the alert.
    assert len(_reports(people["director"])) == 1
    assert _reports(people["director"], "inactivity") == []
    for key in ("away", "dismissed", "disabled", "caller"):
        assert _reports(people[key]) == [] and _reports(people[key], "inactivity") == [], key


def test_muted_reports_and_preference_categories(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    acme = tenants["acme"]
    people = _team(make_user, acme)
    client = client_for(people["silent"], "acme")
    prefs = client.get("/api/v1/notifications/preferences/").json()
    assert prefs["categories"]["report"] is True and prefs["categories"]["inactivity"] is True
    muted = client.post(
        "/api/v1/notifications/preferences/",
        {"category": "report", "enabled": False},
        format="json",
    )
    assert muted.status_code == 200 and muted.json()["categories"]["report"] is False

    with system_tenant_scope("acme"):
        daily_report_service().run(_at(9, 5), ReportSchedule())

    assert _reports(people["silent"]) == []
    assert len(_reports(people["busy"])) == 1


def test_company_report_settings_and_local_time(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acme = tenants["acme"]
    people = _team(make_user, acme)
    director = client_for(people["director"], "acme")
    head = client_for(people["head"], "acme")

    company = director.get("/api/v1/company/").json()
    assert company["morning_report_enabled"] is True and company["morning_report_time"] == "09:00"
    assert company["evening_report_enabled"] is True and company["evening_report_time"] == "19:00"
    seen = head.get("/api/v1/company/")
    assert seen.status_code == 200 and seen.json()["evening_report_time"] == "19:00"
    assert (
        head.patch("/api/v1/company/", {"morning_report_time": "08:00"}, format="json").status_code
        == 403
    )
    for bad in ({"morning_report_time": "25:00"}, {"evening_report_time": "7pm"}):
        assert director.patch("/api/v1/company/", bad, format="json").status_code == 400
    updated = director.patch(
        "/api/v1/company/",
        {
            "timezone": "Asia/Tokyo",
            "morning_report_time": "07:30",
            "evening_report_enabled": False,
        },
        format="json",
    )
    assert updated.status_code == 200, updated.content
    body = updated.json()
    assert body["morning_report_time"] == "07:30" and body["evening_report_enabled"] is False
    assert report_schedule(acme.org.id) == ReportSchedule(
        morning_time=time(7, 30), evening_enabled=False
    )

    # The beat task reads the clock in the company timezone (Tokyo), not the server's.
    tokyo = ZoneInfo("Asia/Tokyo")
    clock = {"now": _at(7, 20, tokyo)}

    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> Any:
            return clock["now"].astimezone(tz)

    monkeypatch.setattr(notification_tasks, "datetime", FrozenClock)
    notification_tasks.daily_reports()
    assert _reports(people["busy"]) == []
    clock["now"] = _at(7, 35, tokyo)
    notification_tasks.daily_reports()
    notification_tasks.daily_reports()
    assert len(_reports(people["busy"])) == 1
    # The evening report is off.
    clock["now"] = _at(19, 5, tokyo)
    notification_tasks.daily_reports()
    assert [r.title[:5] for r in _reports(people["busy"])] == ["Утрен"]
    assert NotificationRecord.objects.using("tenant_globex").count() == 0
