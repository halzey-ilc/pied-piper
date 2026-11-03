"""«Что делать сегодня» (GET /team/today/?include=actions): every group rule, the head of
sales' department scope, the director's company and department views, manager 403,
confirmed-only money, currencies kept apart and tenant isolation."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.activities.infrastructure.models import ActivityRecord
from modules.audit.infrastructure.models import AuditRecord
from modules.leads.infrastructure.models import LeadRecord
from modules.meetings.infrastructure.models import MeetingRecord
from modules.organizations.infrastructure.models import Department, Membership
from modules.payments.infrastructure.models import PaymentRecord
from modules.planning.infrastructure.models import WorkSessionRecord
from modules.sales_pipeline.infrastructure.models import (
    DealRecord,
    DealStageChangeRecord,
    PipelineRecord,
    StageRecord,
)
from platform_core.application.context import TenantContext, tenant_scope
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)

_URL = "/api/v1/team/today/?include=actions&actions_limit=20"


def _in_department(user: Any, tenant: TenantFixture, name: str) -> str:
    department, _ = Department.objects.get_or_create(organization=tenant.org, name=name)
    Membership.objects.filter(user=user, organization=tenant.org).update(department=department)
    return str(department.id)


class _World:
    """Writes tenant records directly, so ages and statuses are exact."""

    def __init__(self, tenant: TenantFixture) -> None:
        self.alias = tenant.alias
        self.context = TenantContext(tenant_id=tenant.org.id, slug=tenant.slug, db_alias=self.alias)
        self.now = datetime.now(UTC)
        with tenant_scope(self.context):
            self._pipeline()

    def _pipeline(self) -> None:
        alias = self.alias
        pipeline = PipelineRecord.objects.using(alias).create(
            id=uuid.uuid4(), name="P", is_default=True, created_at=self.now
        )
        self.pipeline: PipelineRecord = pipeline
        self.stage = StageRecord.objects.using(alias).create(
            id=uuid.uuid4(), pipeline=pipeline, name="Думает", position=1, kind="open"
        )

    def deal(
        self, owner: Any, title: str, *, amount: str = "1000", days_on_stage: int = 0, **kw: Any
    ) -> uuid.UUID:
        deal = DealRecord.objects.using(self.alias).create(
            id=uuid.uuid4(),
            title=title,
            pipeline=self.pipeline,
            stage=self.stage,
            amount=Decimal(amount),
            currency=kw.pop("currency", "KGS"),
            owner_user_id=owner.id,
            created_at=self.now - timedelta(days=days_on_stage),
            **kw,
        )
        return deal.id

    def move(self, deal_id: uuid.UUID, owner: Any, days_ago: int) -> None:
        DealStageChangeRecord.objects.using(self.alias).create(
            id=uuid.uuid4(),
            deal_id=deal_id,
            from_stage_id=self.stage.id,
            to_stage_id=self.stage.id,
            author_user_id=owner.id,
            deal_version=1,
            created_at=self.now - timedelta(days=days_ago),
        )

    def task(
        self, assignee: Any, title: str, due: date | None, *, deal_id: uuid.UUID | None = None
    ) -> uuid.UUID:
        task = ActivityRecord.objects.using(self.alias).create(
            id=uuid.uuid4(),
            kind="task",
            subject_type="deal" if deal_id else "customer",
            subject_id=deal_id or uuid.uuid4(),
            owner_user_id=assignee.id,
            assignee_user_id=assignee.id,
            title=title,
            task_status="open",
            due_date=due,
            created_at=self.now,
        )
        return task.id

    def meeting(
        self,
        owner: Any,
        title: str,
        *,
        start: datetime,
        status: str = "scheduled",
        deal_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        meeting = MeetingRecord.objects.using(self.alias).create(
            id=uuid.uuid4(),
            title=title,
            status=status,
            scheduled_start=start,
            scheduled_end=start + timedelta(hours=1),
            owner_user_id=owner.id,
            subject_type="deal" if deal_id else None,
            subject_id=deal_id,
            created_at=self.now,
        )
        return meeting.id

    def payment(
        self,
        seller: Any,
        amount: str,
        *,
        status: str = "confirmed",
        currency: str = "KGS",
        kind: str = "prepayment",
        deal_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        payment = PaymentRecord.objects.using(self.alias).create(
            id=uuid.uuid4(),
            amount=Decimal(amount),
            currency=currency,
            kind=kind,
            paid_on=self.now.date(),
            seller_user_id=seller.id,
            owner_user_id=seller.id,
            deal_id=deal_id,
            created_at=self.now,
            status=status,
        )
        return payment.id

    def lead(self, owner: Any, name: str, *, minutes_ago: int, status: str = "new") -> uuid.UUID:
        lead = LeadRecord.objects.using(self.alias).create(
            id=uuid.uuid4(),
            name=name,
            source="website",
            status=status,
            owner_user_id=owner.id,
            created_at=self.now - timedelta(minutes=minutes_ago),
        )
        return lead.id


def _groups(response: Any) -> dict[str, dict[str, Any]]:
    assert response.status_code == 200, response.content
    return {g["kind"]: g for g in response.json()["actions"]["groups"]}


def _ids(group: dict[str, Any]) -> set[str]:
    return {item["id"] for item in group["items"]}


def _seed(w: _World, rep: Any, other: Any, today: date) -> dict[str, str]:
    """Records of every rule, inside the tenant scope; returns their ids by name."""
    # 1. Payments waiting for verification, in two currencies; a confirmed one is not listed.
    push_deal = w.deal(rep, "Дожим: предоплата и долг", days_on_stage=20)
    pending_kgs = w.payment(rep, "1000", status="pending_verification", deal_id=push_deal)
    pending_usd = w.payment(rep, "50", status="pending_verification", currency="USD")
    w.payment(rep, "300", deal_id=push_deal)  # confirmed prepayment: debt 700
    pending_south = w.payment(other, "70", status="pending_verification")

    # 2. Overdue tasks and tasks due today/tomorrow (a later one is in neither group).
    overdue = w.task(rep, "Перезвонить", today - timedelta(days=3))
    due_today = w.task(rep, "Отправить КП", today)
    due_tomorrow = w.task(rep, "Созвон", today + timedelta(days=1))
    later = w.task(rep, "Потом", today + timedelta(days=5))
    overdue_south = w.task(other, "Юг", today - timedelta(days=1))

    # 3. Meetings: ended without an outcome is listed; future and held are not.
    no_outcome = w.meeting(rep, "Без итога", start=w.now - timedelta(hours=3))
    future = w.meeting(rep, "Будущая", start=w.now + timedelta(hours=3))
    held_done = w.meeting(rep, "Прошла", start=w.now - timedelta(hours=5), status="held")

    # 4. Deals without a next step: a task with a due date or an upcoming meeting is a step.
    with_task = w.deal(rep, "Есть задача")
    w.task(rep, "Шаг", today + timedelta(days=5), deal_id=with_task)
    undated = w.deal(rep, "Задача без срока")
    w.task(rep, "Когда-нибудь", None, deal_id=undated)
    with_meeting = w.deal(rep, "Встреча впереди")
    w.meeting(rep, "Скоро", start=w.now + timedelta(days=1), deal_id=with_meeting)

    # 5. Deals to push: stalled (>= 14 days on the stage) and a reason.
    after_meeting = w.deal(rep, "После встречи", days_on_stage=30)
    w.meeting(rep, "Была", start=w.now - timedelta(days=25), status="held", deal_id=after_meeting)
    fresh = w.deal(rep, "Свежая после встречи", days_on_stage=2)
    w.meeting(rep, "Была", start=w.now - timedelta(days=1), status="held", deal_id=fresh)
    moved = w.deal(rep, "Недавно двигалась", days_on_stage=40)
    w.move(moved, rep, days_ago=2)
    w.meeting(rep, "Была", start=w.now - timedelta(days=30), status="held", deal_id=moved)
    only_pending = w.deal(rep, "Только непроверенная оплата", amount="500", days_on_stage=20)
    pending_small = w.payment(
        rep, "200", status="pending_verification", kind="additional", deal_id=only_pending
    )
    foreign = w.deal(rep, "Оплата в другой валюте", amount="900", days_on_stage=20)
    w.payment(rep, "100", currency="USD", kind="additional", deal_id=foreign)

    # 6. Leads without a first contact after 15 minutes.
    cold = w.lead(rep, "Холодный", minutes_ago=60)
    too_new = w.lead(rep, "Только пришёл", minutes_ago=5)
    touched = w.lead(rep, "Есть касание", minutes_ago=60)
    ActivityRecord.objects.using(w.alias).create(
        id=uuid.uuid4(),
        kind="note",
        subject_type="lead",
        subject_id=touched,
        owner_user_id=rep.id,
        body="Позвонил",
        created_at=w.now,
    )
    decided = w.lead(rep, "Изменён", minutes_ago=60)
    AuditRecord.objects.using(w.alias).create(
        occurred_at=w.now,
        actor_user_id=rep.id,
        action="lead.updated",
        resource_type="lead",
        resource_id=str(decided),
    )
    qualified = w.lead(rep, "Квалифицирован", minutes_ago=60, status="qualified")

    # 7. Shifts: rep_s is on shift (only "no activity"); rep_n is neither.
    WorkSessionRecord.objects.using(w.alias).create(
        id=uuid.uuid4(), user_id=other.id, started_at=w.now, status="open", created_at=w.now
    )
    return {name: str(value) for name, value in locals().items() if isinstance(value, uuid.UUID)}


def _check_head(head: APIClient, ids: dict[str, str], rep: Any, north: str) -> None:
    # --- head of sales: own department only -------------------------------------------
    response = head.get(_URL)
    groups = _groups(response)
    actions = response.json()["actions"]
    assert [g["kind"] for g in actions["groups"]] == [
        "payments_to_verify",
        "overdue_tasks",
        "tasks_due_soon",
        "meetings_without_outcome",
        "deals_without_next_step",
        "deals_to_push",
        "leads_without_contact",
        "managers_without_activity",
    ]
    assert [g["priority"] for g in actions["groups"]] == list(range(1, 9))
    assert all(g["rule"] and g["title"] for g in actions["groups"])
    assert actions["thresholds"] == {"stalled_deal_days": 14, "lead_response_minutes": 15}
    assert actions["department_id"] == north

    payments = groups["payments_to_verify"]
    assert _ids(payments) == {ids["pending_kgs"], ids["pending_usd"], ids["pending_small"]}
    assert ids["pending_south"] not in _ids(payments)
    # Per currency, never summed across currencies.
    assert payments["amounts"] == [
        {"currency": "KGS", "total": "1200.00"},
        {"currency": "USD", "total": "50.00"},
    ]
    kgs_item = next(i for i in payments["items"] if i["id"] == ids["pending_kgs"])
    assert kgs_item["manager_id"] == str(rep.id) and kgs_item["manager_username"] == "rep_n"
    assert kgs_item["subject_type"] == "deal" and kgs_item["subject_id"] == ids["push_deal"]
    assert kgs_item["title"] == "Дожим: предоплата и долг"

    assert _ids(groups["overdue_tasks"]) == {ids["overdue"]}
    assert groups["overdue_tasks"]["items"][0]["age_days"] == 3
    assert groups["overdue_tasks"]["by_manager"] == [
        {"user_id": str(rep.id), "username": "rep_n", "count": 1}
    ]
    soon = groups["tasks_due_soon"]
    assert [i["id"] for i in soon["items"]] == [ids["due_today"], ids["due_tomorrow"]]
    assert ids["later"] not in _ids(soon)

    assert _ids(groups["meetings_without_outcome"]) == {ids["no_outcome"]}
    assert not {ids["future"], ids["held_done"]} & _ids(groups["meetings_without_outcome"])
    _check_head_deals(groups, ids, rep)


def _check_head_deals(groups: dict[str, dict[str, Any]], ids: dict[str, str], rep: Any) -> None:
    no_step = _ids(groups["deals_without_next_step"])
    assert ids["undated"] in no_step
    assert ids["with_task"] not in no_step and ids["with_meeting"] not in no_step

    push = {i["id"]: i for i in groups["deals_to_push"]["items"]}
    assert set(push) == {ids["push_deal"], ids["after_meeting"]}
    assert push[ids["push_deal"]]["reasons"] == ["prepayment", "debt"]
    assert push[ids["push_deal"]]["paid"] == "300.00" and push[ids["push_deal"]]["debt"] == "700.00"
    assert push[ids["after_meeting"]]["reasons"] == ["after_meeting"]
    assert push[ids["after_meeting"]]["age_days"] >= 29
    # Pending money is not paid; money in another currency is not compared with the amount.
    assert ids["only_pending"] not in push and ids["foreign"] not in push
    assert ids["fresh"] not in push and ids["moved"] not in push
    assert groups["deals_to_push"]["amounts"] == [{"currency": "KGS", "total": "700.00"}]

    leads = _ids(groups["leads_without_contact"])
    assert leads == {ids["cold"]}
    assert not {ids["too_new"], ids["touched"], ids["decided"], ids["qualified"]} & leads

    managers = {i["id"]: i for i in groups["managers_without_activity"]["items"]}
    assert set(managers) == {str(rep.id)}  # the head of sales is not on the list
    # Confirmed payments today may count as activity, depending on the company-day boundary.
    assert managers[str(rep.id)]["reasons"][0] == "not_on_shift"


def _check_director(
    director: APIClient, head: APIClient, ids: dict[str, str], other: Any, south: str
) -> None:
    # --- director: whole company, then one department ------------------------------------
    whole = _groups(director.get(_URL))
    assert _ids(whole["overdue_tasks"]) == {ids["overdue"], ids["overdue_south"]}
    assert ids["pending_south"] in _ids(whole["payments_to_verify"])
    assert {"currency": "KGS", "total": "1270.00"} in whole["payments_to_verify"]["amounts"]
    silent = {i["id"]: i["reasons"] for i in whole["managers_without_activity"]["items"]}
    assert silent[str(other.id)] == ["no_activity"]

    south_only = _groups(director.get(f"{_URL}&department_id={south}"))
    assert _ids(south_only["overdue_tasks"]) == {ids["overdue_south"]}
    assert _ids(south_only["payments_to_verify"]) == {ids["pending_south"]}
    assert south_only["deals_to_push"]["count"] == 0
    assert south_only["deals_to_push"]["items"] == []

    # A head of sales cannot look at another department.
    pinned = _groups(head.get(f"{_URL}&department_id={south}"))
    assert ids["overdue_south"] not in _ids(pinned["overdue_tasks"])

    # The limit caps items, not the count.
    capped = _groups(director.get("/api/v1/team/today/?include=actions&actions_limit=1"))
    assert capped["tasks_due_soon"]["count"] == 2 and len(capped["tasks_due_soon"]["items"]) == 1
    assert director.get("/api/v1/team/today/?include=actions&actions_limit=0").status_code == 400

    # Without include=actions the board is unchanged.
    assert "actions" not in director.get("/api/v1/team/today/").json()


def test_today_actions_rules_scope_and_money(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    acme = tenants["acme"]
    director = client_for(make_user("boss", tenant=acme, role=Role.DIRECTOR), "acme")
    head_user = make_user("head", tenant=acme, role=Role.HEAD_OF_SALES)
    rep = make_user("rep_n", tenant=acme, role=Role.SALES_REP)
    other = make_user("rep_s", tenant=acme, role=Role.SALES_REP)
    north = _in_department(head_user, acme, "North")
    _in_department(rep, acme, "North")
    south = _in_department(other, acme, "South")
    head = client_for(head_user, "acme")

    w = _World(acme)
    with tenant_scope(w.context):
        ids = _seed(w, rep, other, w.now.astimezone(ZoneInfo(acme.org.timezone)).date())
    _check_head(head, ids, rep, north)
    _check_director(director, head, ids, other, south)


def test_today_actions_manager_forbidden_and_tenant_isolation(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    acme, globex = tenants["acme"], tenants["globex"]
    rep = make_user("rep", tenant=acme, role=Role.SALES_REP)
    assert client_for(rep, "acme").get(_URL).status_code == 403

    w = _World(acme)
    with tenant_scope(w.context):
        overdue = w.task(rep, "Просрочена", w.now.date() - timedelta(days=5))
        pending = w.payment(rep, "10", status="pending_verification")

    rival = client_for(make_user("rival", tenant=globex, role=Role.DIRECTOR), "globex")
    groups = _groups(rival.get(_URL))
    assert str(overdue) not in _ids(groups["overdue_tasks"])
    assert str(pending) not in _ids(groups["payments_to_verify"])
    assert all(g["count"] == 0 for g in groups.values())
    # A member of acme cannot read globex through the header either.
    assert client_for(rep, "globex").get(_URL).status_code in (403, 404)
