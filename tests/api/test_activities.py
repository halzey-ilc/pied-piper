"""Activities API: logging kinds, timeline, task lifecycle, subject checks, RBAC, isolation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.activities.infrastructure.models import ActivityRecord
from platform_core.infrastructure.messaging.models import OutboxMessage
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)


def _customer(client: APIClient, name: str = "Acme") -> str:
    response = client.post("/api/v1/customers/", {"name": name}, format="json")
    assert response.status_code == 201, response.content
    customer_id: str = response.json()["id"]
    return customer_id


def _log(client: APIClient, **body: Any) -> Any:
    return client.post("/api/v1/activities/", body, format="json")


def test_log_each_kind_on_a_customer(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    cid = _customer(client)

    task = _log(client, kind="task", subject_type="customer", subject_id=cid, title="Follow up")
    assert task.status_code == 201 and task.json()["task_status"] == "open"

    call = _log(
        client,
        kind="call",
        subject_type="customer",
        subject_id=cid,
        direction="outbound",
        occurred_at="2026-10-28T10:00:00Z",
        duration_seconds=90,
    )
    assert call.status_code == 201 and call.json()["duration_seconds"] == 90

    note = _log(client, kind="note", subject_type="customer", subject_id=cid, body="Spoke to CFO")
    assert note.status_code == 201 and note.json()["body"] == "Spoke to CFO"

    assert ActivityRecord.objects.using("tenant_acme").count() == 3
    events = list(OutboxMessage.objects.using("tenant_acme").values_list("event_type", flat=True))
    assert events.count("activities.activity_logged") == 3


def test_missing_kind_fields_are_rejected(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    cid = _customer(client)
    # task without title
    assert _log(client, kind="task", subject_type="customer", subject_id=cid).status_code == 400
    # call without direction/occurred_at
    assert _log(client, kind="call", subject_type="customer", subject_id=cid).status_code == 400
    # note without body
    assert _log(client, kind="note", subject_type="customer", subject_id=cid).status_code == 400


def test_subject_must_exist(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    response = _log(
        client,
        kind="note",
        subject_type="customer",
        subject_id="00000000-0000-0000-0000-000000000000",
        body="orphan",
    )
    assert response.status_code == 404
    assert ActivityRecord.objects.using("tenant_acme").count() == 0


def test_task_complete_reopen_lifecycle(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    cid = _customer(client)
    task_id = _log(client, kind="task", subject_type="customer", subject_id=cid, title="T").json()[
        "id"
    ]

    completed = client.post(f"/api/v1/activities/{task_id}/complete/", {}, format="json")
    assert completed.status_code == 200 and completed.json()["task_status"] == "completed"
    # completing again conflicts
    assert (
        client.post(f"/api/v1/activities/{task_id}/complete/", {}, format="json").status_code == 409
    )
    reopened = client.post(f"/api/v1/activities/{task_id}/reopen/", {}, format="json")
    assert reopened.status_code == 200 and reopened.json()["task_status"] == "open"

    note_id = _log(client, kind="note", subject_type="customer", subject_id=cid, body="n").json()[
        "id"
    ]
    assert (
        client.post(f"/api/v1/activities/{note_id}/complete/", {}, format="json").status_code == 409
    )


def test_timeline_filters_by_subject_and_kind(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    a = _customer(client, "A")
    b = _customer(client, "B")
    _log(client, kind="note", subject_type="customer", subject_id=a, body="a1")
    _log(client, kind="task", subject_type="customer", subject_id=a, title="a-task")
    _log(client, kind="note", subject_type="customer", subject_id=b, body="b1")

    a_timeline = client.get(f"/api/v1/activities/?subject_type=customer&subject_id={a}").json()
    assert a_timeline["count"] == 2
    a_notes = client.get(
        f"/api/v1/activities/?subject_type=customer&subject_id={a}&kind=note"
    ).json()
    assert a_notes["count"] == 1 and a_notes["results"][0]["body"] == "a1"
    assert client.get("/api/v1/activities/").json()["count"] == 3


def test_rbac_scope_and_roles(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    rep = client_for(make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP), "acme")
    viewer = client_for(make_user("viewer", tenant=tenants["acme"], role=Role.VIEWER), "acme")
    cid = _customer(owner)

    owners_note = _log(
        owner, kind="note", subject_type="customer", subject_id=cid, body="owner"
    ).json()["id"]
    reps_note = _log(rep, kind="note", subject_type="customer", subject_id=cid, body="rep").json()[
        "id"
    ]

    assert [a["id"] for a in rep.get("/api/v1/activities/").json()["results"]] == [reps_note]
    assert rep.get(f"/api/v1/activities/{owners_note}/").status_code == 404
    assert viewer.get("/api/v1/activities/").json()["count"] == 2
    assert (
        _log(viewer, kind="note", subject_type="customer", subject_id=cid, body="x").status_code
        == 403
    )


def test_cross_tenant_isolation(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    alice = make_user("alice", tenant=tenants["acme"])
    bob = make_user("bob", tenant=tenants["globex"])
    bob_client = client_for(bob, "globex")
    bob_cid = _customer(bob_client, "Globex")
    bob_note = _log(
        bob_client, kind="note", subject_type="customer", subject_id=bob_cid, body="secret"
    ).json()["id"]

    assert client_for(alice, "globex").get("/api/v1/activities/").status_code == 404
    assert client_for(alice, "acme").get(f"/api/v1/activities/{bob_note}/").status_code == 404
    assert ActivityRecord.objects.using("tenant_globex").count() == 1
    assert ActivityRecord.objects.using("tenant_acme").count() == 0
