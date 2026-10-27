"""Meetings API: scheduling, participants, lifecycle, link validation, RBAC, isolation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.meetings.infrastructure.models import MeetingRecord
from platform_core.infrastructure.messaging.models import OutboxMessage
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)

_START = "2026-11-05T10:00:00Z"
_END = "2026-11-05T10:30:00Z"


def _customer(client: APIClient, name: str = "Acme") -> str:
    cid: str = client.post("/api/v1/customers/", {"name": name}, format="json").json()["id"]
    return cid


def _schedule(client: APIClient, **body: Any) -> Any:
    body.setdefault("title", "Kickoff")
    body.setdefault("scheduled_start", _START)
    body.setdefault("scheduled_end", _END)
    return client.post("/api/v1/meetings/", body, format="json")


def test_schedule_with_participants_and_reminders(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    cid = _customer(client)
    response = _schedule(
        client,
        subject_type="customer",
        subject_id=cid,
        participants=[{"name": "Jane", "email": "jane@example.test"}, {"name": "Bob"}],
        reminders=[10, 60],
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["status"] == "scheduled"
    assert body["subject_id"] == cid
    assert body["reminders"] == [60, 10]
    assert len(body["participants"]) == 2
    assert (
        OutboxMessage.objects.using("tenant_acme")
        .filter(event_type="meetings.meeting_scheduled")
        .exists()
    )


def test_end_before_start_rejected(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    response = _schedule(client, scheduled_start=_END, scheduled_end=_START)
    assert response.status_code == 400


def test_linked_subject_must_exist(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    response = _schedule(
        client, subject_type="customer", subject_id="00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 404
    assert MeetingRecord.objects.using("tenant_acme").count() == 0


def test_lifecycle_reschedule_complete(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    meeting_id = _schedule(client).json()["id"]

    rescheduled = client.post(
        f"/api/v1/meetings/{meeting_id}/reschedule/",
        {"scheduled_start": "2026-11-06T09:00:00Z", "scheduled_end": "2026-11-06T09:45:00Z"},
        format="json",
    )
    assert rescheduled.status_code == 200
    assert rescheduled.json()["scheduled_start"].startswith("2026-11-06T09:00")

    completed = client.post(
        f"/api/v1/meetings/{meeting_id}/complete/", {"outcome": "agreed next steps"}, format="json"
    )
    assert completed.status_code == 200 and completed.json()["status"] == "completed"
    # A completed meeting is terminal.
    assert (
        client.post(
            f"/api/v1/meetings/{meeting_id}/cancel/", {"reason": "x"}, format="json"
        ).status_code
        == 409
    )
    events = set(OutboxMessage.objects.using("tenant_acme").values_list("event_type", flat=True))
    assert {
        "meetings.meeting_scheduled",
        "meetings.meeting_rescheduled",
        "meetings.meeting_completed",
    } <= events


def test_participants_add_and_respond(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    meeting_id = _schedule(client).json()["id"]
    added = client.post(
        f"/api/v1/meetings/{meeting_id}/participants/",
        {"name": "Jane", "email": "jane@example.test"},
        format="json",
    )
    assert added.status_code == 200 and len(added.json()["participants"]) == 1
    responded = client.post(
        f"/api/v1/meetings/{meeting_id}/participants/respond/",
        {"key": "jane@example.test", "response": "accepted"},
        format="json",
    )
    assert responded.json()["participants"][0]["response"] == "accepted"


def test_rbac_scope_and_roles(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    rep = client_for(make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP), "acme")
    viewer = client_for(make_user("viewer", tenant=tenants["acme"], role=Role.VIEWER), "acme")

    owners_meeting = _schedule(owner, title="Owner mtg").json()["id"]
    reps_meeting = _schedule(rep, title="Rep mtg").json()["id"]

    assert [m["id"] for m in rep.get("/api/v1/meetings/").json()["results"]] == [reps_meeting]
    assert rep.get(f"/api/v1/meetings/{owners_meeting}/").status_code == 404
    assert viewer.get("/api/v1/meetings/").json()["count"] == 2
    assert _schedule(viewer).status_code == 403


def test_cross_tenant_isolation(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    alice = make_user("alice", tenant=tenants["acme"])
    bob = make_user("bob", tenant=tenants["globex"])
    bob_meeting = _schedule(client_for(bob, "globex"), title="Globex mtg").json()["id"]

    assert client_for(alice, "globex").get("/api/v1/meetings/").status_code == 404
    assert client_for(alice, "acme").get(f"/api/v1/meetings/{bob_meeting}/").status_code == 404
    assert MeetingRecord.objects.using("tenant_globex").get().title == "Globex mtg"
    assert MeetingRecord.objects.using("tenant_acme").count() == 0
