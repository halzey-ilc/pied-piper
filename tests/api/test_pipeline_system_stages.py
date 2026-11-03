"""Pipeline system stages and stage-move rules (owner decisions of 03.11.2026):
«Продажа» is locked, last and reached only by a confirmed payment; «Встреча перенесена»
can be moved and switched off and reschedules the deal's meeting; a move to another open
stage and a held meeting need a planned next step."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from django.apps import apps
from django.utils import timezone
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.activities.infrastructure.models import ActivityRecord
from modules.meetings.infrastructure.models import MeetingRecord
from modules.sales_pipeline.infrastructure.models import DealRecord, PipelineRecord, StageRecord
from tests.api.sale_helpers import close_as_sale, next_step
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)
ALIAS = "tenant_acme"

_STAGES = [
    {"name": "Новый", "kind": "open"},
    {"name": "Встреча", "kind": "open"},
    {"name": "Успешно", "kind": "won"},
    {"name": "Отказ", "kind": "lost"},
]


def _ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.content
    return response.json() if response.content else None


def _error(response: Any, status: int, code: str) -> None:
    assert response.status_code == status, response.content
    assert response.json()["error"]["code"] == code


def _setup(make_user: Callable[..., Any], tenants: dict[str, TenantFixture], client_for: Any):
    owner = make_user("owner", tenant=tenants["acme"], role=Role.DIRECTOR)
    client = client_for(owner, "acme")
    pipeline = _ok(
        client.post(
            "/api/v1/sales/pipelines/",
            {"name": "Продажи", "is_default": True, "stages": _STAGES},
            format="json",
        ),
        201,
    )
    stages = {s["name"]: s for s in pipeline["stages"]}
    return owner, client, pipeline, stages


def _deal(client: APIClient, title: str = "Сделка") -> str:
    deal_id: str = _ok(
        client.post("/api/v1/sales/deals/", {"title": title, "amount": "100.00"}, format="json"),
        201,
    )["id"]
    return deal_id


def _move(client: APIClient, deal_id: str, stage_id: str, **extra: Any) -> Any:
    return client.post(
        f"/api/v1/sales/deals/{deal_id}/stage/", {"stage_id": stage_id, **extra}, format="json"
    )


def _deal_tasks(deal_id: str) -> list[ActivityRecord]:
    return list(
        ActivityRecord.objects.using(ALIAS).filter(
            kind="task", subject_type="deal", subject_id=deal_id
        )
    )


# --- the stages themselves ------------------------------------------------------------


def test_new_pipeline_gets_both_system_stages_with_sale_last(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    _owner, _client, pipeline, _stages = _setup(make_user, tenants, client_for)
    rows = [(s["name"], s["kind"], s["system_code"]) for s in pipeline["stages"]]
    # The first won stage became «Продажа» and moved to the end; «Встреча перенесена»
    # stands after the open stages.
    assert rows == [
        ("Новый", "open", None),
        ("Встреча", "open", None),
        ("Встреча перенесена", "open", "meeting_rescheduled"),
        ("Отказ", "lost", None),
        ("Продажа", "won", "sale"),
    ]


def test_sale_stage_is_locked(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    _owner, client, pipeline, stages = _setup(make_user, tenants, client_for)
    url = f"/api/v1/sales/pipelines/{pipeline['id']}/"
    sale = stages["Продажа"]["id"]
    _error(
        client.patch(f"{url}stages/{sale}/", {"name": "Оплата"}, format="json"),
        400,
        "system_stage_locked",
    )
    _error(
        client.patch(f"{url}stages/{sale}/", {"is_active": False}, format="json"),
        400,
        "system_stage_locked",
    )
    _error(client.delete(f"{url}stages/{sale}/"), 400, "system_stage_locked")
    order = [s["id"] for s in pipeline["stages"]]
    _error(
        client.put(f"{url}stage-order/", {"stage_ids": [sale, *order[:-1]]}, format="json"),
        400,
        "system_stage_locked",
    )
    # A new stage is never placed after «Продажа».
    added = _ok(client.post(f"{url}stages/", {"name": "Счёт", "position": 99}, format="json"), 201)
    assert added["stages"][-1]["id"] == sale
    # The probability may still be tuned.
    tuned = _ok(client.patch(f"{url}stages/{sale}/", {"probability": 95}, format="json"))
    assert {s["id"]: s["probability"] for s in tuned["stages"]}[sale] == 95


def test_meeting_rescheduled_moves_and_switches_but_keeps_its_name(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    _owner, client, pipeline, stages = _setup(make_user, tenants, client_for)
    url = f"/api/v1/sales/pipelines/{pipeline['id']}/"
    rescheduled = stages["Встреча перенесена"]["id"]
    _error(
        client.patch(f"{url}stages/{rescheduled}/", {"name": "Перенос"}, format="json"),
        400,
        "system_stage_locked",
    )
    _error(client.delete(f"{url}stages/{rescheduled}/"), 400, "system_stage_locked")

    order = [s["id"] for s in pipeline["stages"]]
    moved = _ok(
        client.put(
            f"{url}stage-order/",
            {"stage_ids": [order[0], rescheduled, order[1], *order[3:]]},
            format="json",
        )
    )
    assert [s["id"] for s in moved["stages"]][1] == rescheduled

    off = _ok(client.patch(f"{url}stages/{rescheduled}/", {"is_active": False}, format="json"))
    assert {s["id"]: s["is_active"] for s in off["stages"]}[rescheduled] is False
    deal_id = _deal(client)
    _error(
        _move(
            client,
            deal_id,
            rescheduled,
            meeting_start="2030-01-10T09:00:00Z",
            meeting_end="2030-01-10T10:00:00Z",
        ),
        400,
        "validation_error",
    )
    on = _ok(client.patch(f"{url}stages/{rescheduled}/", {"is_active": True}, format="json"))
    assert {s["id"]: s["is_active"] for s in on["stages"]}[rescheduled] is True


def test_managers_cannot_change_system_stages(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    _owner, _client, pipeline, stages = _setup(make_user, tenants, client_for)
    rep = client_for(make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP), "acme")
    url = f"/api/v1/sales/pipelines/{pipeline['id']}/stages/{stages['Встреча перенесена']['id']}/"
    assert rep.patch(url, {"is_active": False}, format="json").status_code == 403


# --- «Продажа» --------------------------------------------------------------------------


def test_sale_requires_a_payment(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    _owner, client, _pipeline, stages = _setup(make_user, tenants, client_for)
    deal_id = _deal(client)
    _error(_move(client, deal_id, stages["Продажа"]["id"]), 409, "sale_requires_payment")
    _error(client.post(f"/api/v1/sales/deals/{deal_id}/win/"), 409, "sale_requires_payment")
    assert DealRecord.objects.using(ALIAS).get(id=deal_id).status == "open"

    closed = close_as_sale(deal_id)
    assert closed == {"status": "won", "stage_id": stages["Продажа"]["id"]}
    history = _ok(client.get(f"/api/v1/sales/deals/{deal_id}/stage-history/"))["results"]
    assert history[-1]["to_stage_id"] == stages["Продажа"]["id"]


def test_any_won_stage_requires_a_payment(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    """A second won stage the ROP added is not a back door around the payment."""
    owner = make_user("owner", tenant=tenants["acme"], role=Role.DIRECTOR)
    client = client_for(owner, "acme")
    stages = [*_STAGES, {"name": "Оплачено наличными", "kind": "won"}]
    pipeline = _ok(
        client.post(
            "/api/v1/sales/pipelines/",
            {"name": "Продажи", "is_default": True, "stages": stages},
            format="json",
        ),
        201,
    )
    extra = next(s for s in pipeline["stages"] if s["kind"] == "won" and s["system_code"] != "sale")
    deal_id = _deal(client)
    _error(_move(client, deal_id, extra["id"]), 409, "sale_requires_payment")
    assert DealRecord.objects.using(ALIAS).get(id=deal_id).status == "open"


# --- «Встреча перенесена» -------------------------------------------------------------


def _meeting(client: APIClient, deal_id: str, start: str, end: str) -> str:
    meeting_id: str = _ok(
        client.post(
            "/api/v1/meetings/",
            {
                "title": "Встреча",
                "scheduled_start": start,
                "scheduled_end": end,
                "subject_type": "deal",
                "subject_id": deal_id,
            },
            format="json",
        ),
        201,
    )["id"]
    return meeting_id


def test_meeting_rescheduled_needs_the_new_time(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    _owner, client, _pipeline, stages = _setup(make_user, tenants, client_for)
    deal_id = _deal(client)
    target = stages["Встреча перенесена"]["id"]
    _error(_move(client, deal_id, target), 400, "meeting_date_required")
    _error(
        _move(client, deal_id, target, meeting_start="2030-01-10T09:00:00Z"),
        400,
        "meeting_date_required",
    )
    # A bad window is refused and nothing moves (one transaction).
    _error(
        _move(
            client,
            deal_id,
            target,
            meeting_start="2030-01-10T10:00:00Z",
            meeting_end="2030-01-10T09:00:00Z",
        ),
        400,
        "validation_error",
    )
    assert str(DealRecord.objects.using(ALIAS).get(id=deal_id).stage_id) == stages["Новый"]["id"]
    assert MeetingRecord.objects.using(ALIAS).count() == 0


def test_meeting_rescheduled_moves_the_nearest_meeting_and_keeps_the_old_date(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    _owner, client, _pipeline, stages = _setup(make_user, tenants, client_for)
    deal_id = _deal(client)
    nearest = _meeting(client, deal_id, "2030-01-05T09:00:00Z", "2030-01-05T10:00:00Z")
    later = _meeting(client, deal_id, "2030-02-05T09:00:00Z", "2030-02-05T10:00:00Z")

    moved = _ok(
        _move(
            client,
            deal_id,
            stages["Встреча перенесена"]["id"],
            meeting_start="2030-01-12T14:00:00Z",
            meeting_end="2030-01-12T15:00:00Z",
        )
    )
    assert moved["stage_id"] == stages["Встреча перенесена"]["id"]
    assert _ok(client.get(f"/api/v1/meetings/{nearest}/"))["scheduled_start"].startswith(
        "2030-01-12T14:00"
    )
    assert _ok(client.get(f"/api/v1/meetings/{later}/"))["scheduled_start"].startswith(
        "2030-02-05T09:00"
    )
    history = _ok(client.get(f"/api/v1/sales/deals/{deal_id}/stage-history/"))["results"]
    assert (
        history[-1]["reason"] == "Встреча перенесена: 05.01.2030 09:00 UTC → 12.01.2030 14:00 UTC"
    )
    audit = _ok(client.get(f"/api/v1/sales/deals/{deal_id}/history/"))["results"]
    assert "deal.meeting_rescheduled" in {h["action"] for h in audit}
    # The new meeting itself is the planned next step: no task is demanded.
    assert _deal_tasks(deal_id) == []


def test_meeting_rescheduled_books_a_meeting_for_the_owner_when_there_is_none(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner, client, _pipeline, stages = _setup(make_user, tenants, client_for)
    deal_id = _deal(client)
    _ok(
        _move(
            client,
            deal_id,
            stages["Встреча перенесена"]["id"],
            meeting_start="2030-01-12T14:00:00Z",
            meeting_end="2030-01-12T15:00:00Z",
        )
    )
    (meeting,) = MeetingRecord.objects.using(ALIAS).all()
    assert str(meeting.subject_id) == deal_id and meeting.host_user_id == owner.id
    history = _ok(client.get(f"/api/v1/sales/deals/{deal_id}/stage-history/"))["results"]
    assert history[-1]["reason"] == "Встреча назначена на 12.01.2030 14:00 UTC"


# --- next step ------------------------------------------------------------------------


def test_moving_to_an_open_stage_needs_a_next_step(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner, client, _pipeline, stages = _setup(make_user, tenants, client_for)
    deal_id = _deal(client)
    _error(_move(client, deal_id, stages["Встреча"]["id"]), 400, "next_step_required")
    assert str(DealRecord.objects.using(ALIAS).get(id=deal_id).stage_id) == stages["Новый"]["id"]

    _ok(_move(client, deal_id, stages["Встреча"]["id"], next_step=next_step("Отправить КП")))
    (task,) = _deal_tasks(deal_id)
    assert task.title == "Отправить КП" and str(task.due_date) == "2030-01-10"
    assert task.task_status == "open" and task.assignee_user_id == owner.id

    # An open task with a due date is already the next step: back to «Новый» needs nothing.
    _ok(_move(client, deal_id, stages["Новый"]["id"]))
    assert len(_deal_tasks(deal_id)) == 1
    # Closing moves (lost) never ask for one.
    _ok(_move(client, deal_id, stages["Отказ"]["id"], reason="Дорого"))


def test_next_step_goes_to_the_named_member_and_a_refused_task_keeps_the_deal(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    _owner, client, _pipeline, stages = _setup(make_user, tenants, client_for)
    rep = make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP)
    deal_id = _deal(client)
    # A stranger cannot be assigned: the task is refused and the move rolls back.
    stranger = make_user("stranger")
    refused = _move(
        client,
        deal_id,
        stages["Встреча"]["id"],
        next_step={**next_step(), "assignee_user_id": str(stranger.id)},
    )
    assert refused.status_code in (400, 403, 404), refused.content
    assert str(DealRecord.objects.using(ALIAS).get(id=deal_id).stage_id) == stages["Новый"]["id"]
    assert _deal_tasks(deal_id) == []

    _ok(
        _move(
            client,
            deal_id,
            stages["Встреча"]["id"],
            next_step={**next_step(), "assignee_user_id": str(rep.id)},
        )
    )
    (task,) = _deal_tasks(deal_id)
    assert task.assignee_user_id == rep.id


def test_a_held_meeting_needs_a_next_step(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    _owner, client, _pipeline, _stages = _setup(make_user, tenants, client_for)
    deal_id = _deal(client)
    meeting_id = _meeting(client, deal_id, "2030-01-05T09:00:00Z", "2030-01-05T10:00:00Z")
    url = f"/api/v1/meetings/{meeting_id}/outcome/"
    _error(client.post(url, {"outcome": "held"}, format="json"), 400, "next_step_required")
    assert MeetingRecord.objects.using(ALIAS).get(id=meeting_id).status == "scheduled"
    _error(
        client.post(f"/api/v1/meetings/{meeting_id}/complete/", {}, format="json"),
        400,
        "next_step_required",
    )

    held = _ok(
        client.post(
            url, {"outcome": "held", "next_step": next_step("Счёт на оплату")}, format="json"
        )
    )
    assert held["status"] == "held"
    (task,) = _deal_tasks(deal_id)
    assert task.title == "Счёт на оплату"

    # Other outcomes do not ask for a next step.
    other = _meeting(client, deal_id, "2030-01-06T09:00:00Z", "2030-01-06T10:00:00Z")
    _ok(client.post(f"/api/v1/meetings/{other}/outcome/", {"outcome": "no_show"}, format="json"))


# --- data migration -------------------------------------------------------------------


def _legacy_pipeline(stages: list[tuple[str, str]]) -> PipelineRecord:
    pipeline = PipelineRecord.objects.using(ALIAS).create(
        id=uuid4(), name=f"Old {uuid4().hex[:6]}", created_at=timezone.now()
    )
    for position, (name, kind) in enumerate(stages):
        StageRecord.objects.using(ALIAS).create(
            id=uuid4(),
            pipeline_id=pipeline.id,
            name=name,
            position=position,
            probability=0,
            kind=kind,
        )
    return pipeline


def _migrate() -> None:
    migration = importlib.import_module(
        "modules.sales_pipeline.infrastructure.migrations.0008_stage_system_code"
    )
    editor = SimpleNamespace(connection=SimpleNamespace(alias=ALIAS))
    migration._add_system_stages(apps, editor)


def _rows(pipeline: PipelineRecord) -> list[tuple[str, str, str | None]]:
    return [
        (s.name, s.kind, s.system_code)
        for s in StageRecord.objects.using(ALIAS)
        .filter(pipeline_id=pipeline.id)
        .order_by("position")
    ]


def test_migration_turns_the_first_won_stage_into_sale(tenants: dict[str, TenantFixture]) -> None:
    pipeline = _legacy_pipeline(
        [("Новый", "open"), ("Выиграна", "won"), ("Повторная", "won"), ("Проиграна", "lost")]
    )
    won_id = StageRecord.objects.using(ALIAS).get(pipeline_id=pipeline.id, name="Выиграна").id
    _migrate()
    assert _rows(pipeline) == [
        ("Новый", "open", None),
        ("Встреча перенесена", "open", "meeting_rescheduled"),
        ("Повторная", "won", None),
        ("Проиграна", "lost", None),
        ("Продажа", "won", "sale"),
    ]
    # The same row (and id) — deals standing on it keep their stage.
    assert StageRecord.objects.using(ALIAS).get(id=won_id).system_code == "sale"
    _migrate()  # idempotent
    assert len(_rows(pipeline)) == 5


def test_migration_creates_sale_when_there_is_no_won_stage(
    tenants: dict[str, TenantFixture],
) -> None:
    pipeline = _legacy_pipeline(
        [("Новый", "open"), ("Встреча перенесена", "open"), ("Продажа", "lost")]
    )
    _migrate()
    assert _rows(pipeline) == [
        ("Новый", "open", None),
        ("Встреча перенесена", "open", "meeting_rescheduled"),
        ("Продажа (2)", "lost", None),
        ("Продажа", "won", "sale"),
    ]
