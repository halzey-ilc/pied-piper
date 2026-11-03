"""Customer cards: merging duplicates; calls: filters, linking and the missed-call follow-up."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from entrypoints.workers.tasks.events import _dispatch
from modules.access_control.domain.rbac import Role
from modules.activities.infrastructure.models import ActivityRecord
from modules.notifications.infrastructure.models import NotificationRecord
from platform_core.infrastructure.messaging.models import OutboxMessage
from platform_core.infrastructure.tenancy.services import system_tenant_scope
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)
ALIAS = "tenant_acme"


def _ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.content
    return response.json() if response.content else None


def _customer(client: APIClient, **body: Any) -> dict[str, Any]:
    return _ok(client.post("/api/v1/customers/", body, format="json"), 201)  # type: ignore[no-any-return]


def _call(client: APIClient, **body: Any) -> dict[str, Any]:
    body.setdefault("direction", "inbound")
    body.setdefault("from_number", "+77011112233")
    body.setdefault("to_number", "+77019998877")
    body.setdefault("status", "completed")
    body.setdefault("started_at", "2026-10-28T09:00:00Z")
    body.setdefault("duration_seconds", 60)
    return _ok(client.post("/api/v1/telephony/calls/", body, format="json"), 201)  # type: ignore[no-any-return]


def _pipeline(client: APIClient) -> None:
    stages = [{"name": "New"}, {"name": "Won", "kind": "won"}, {"name": "Lost", "kind": "lost"}]
    body = {"name": "Main", "is_default": True, "stages": stages}
    _ok(client.post("/api/v1/sales/pipelines/", body, format="json"), 201)


def test_merge_moves_everything_and_keeps_the_duplicate_as_history(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    _pipeline(owner)
    kept = _customer(owner, name="Acme LLC")
    dup = _customer(owner, name="ACME", email="sales@acme.test", phone="+77011112233")
    deal = _ok(
        owner.post(
            "/api/v1/sales/deals/",
            {"title": "Deal", "amount": "100.00", "customer_id": dup["id"]},
            format="json",
        ),
        201,
    )
    _ok(
        owner.post(
            "/api/v1/activities/",
            {"kind": "note", "subject_type": "customer", "subject_id": dup["id"], "body": "Hi"},
            format="json",
        ),
        201,
    )
    _call(owner, subject_type="customer", subject_id=dup["id"])

    preview = _ok(owner.get(f"/api/v1/customers/{kept['id']}/merge/?source_id={dup['id']}"))
    assert preview["references"]["deals"] == 1
    assert preview["references"]["activities"] == 1
    assert preview["references"]["calls"] == 1
    assert set(preview["fills"]) == {"email", "phone"}
    # The preview changed nothing.
    assert _ok(owner.get(f"/api/v1/sales/deals/{deal['id']}/"))["customer_id"] == dup["id"]

    merged = _ok(
        owner.post(
            f"/api/v1/customers/{kept['id']}/merge/", {"source_id": dup["id"]}, format="json"
        )
    )
    assert merged["target"]["email"] == "sales@acme.test"
    assert merged["target"]["phone"] == "+77011112233"
    assert merged["source"]["merged_into_id"] == kept["id"]
    assert merged["source"]["archived_at"] is not None
    assert _ok(owner.get(f"/api/v1/sales/deals/{deal['id']}/"))["customer_id"] == kept["id"]
    timeline = _ok(owner.get(f"/api/v1/activities/?subject_type=customer&subject_id={kept['id']}"))
    assert timeline["count"] == 1
    calls = _ok(
        owner.get(f"/api/v1/telephony/calls/?subject_type=customer&subject_id={kept['id']}")
    )
    assert calls["count"] == 1
    # The duplicate is history: still readable, cannot be restored or merged again.
    assert owner.get(f"/api/v1/customers/{dup['id']}/").status_code == 200
    assert owner.post(f"/api/v1/customers/{dup['id']}/restore/").status_code == 409
    again = owner.post(
        f"/api/v1/customers/{kept['id']}/merge/", {"source_id": dup["id"]}, format="json"
    )
    assert again.status_code == 409
    same = owner.post(
        f"/api/v1/customers/{kept['id']}/merge/", {"source_id": kept["id"]}, format="json"
    )
    assert same.status_code == 400


def test_a_sales_rep_cannot_merge(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    rep = client_for(make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP), "acme")
    a = _customer(rep, name="A")
    b = _customer(rep, name="B")
    denied = rep.post(f"/api/v1/customers/{a['id']}/merge/", {"source_id": b["id"]}, format="json")
    assert denied.status_code == 403


def test_call_filters_and_linking(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner_user = make_user("owner", tenant=tenants["acme"])
    owner = client_for(owner_user, "acme")
    customer = _customer(owner, name="Linked")
    missed = _call(owner, status="missed", duration_seconds=0, from_number="+77015550001")
    _call(owner, status="failed", duration_seconds=0, started_at="2026-10-20T09:00:00Z")
    _call(owner, notes="Asked about the contract price")

    def ids(query: str) -> set[str]:
        return {c["id"] for c in _ok(owner.get(f"/api/v1/telephony/calls/?{query}"))["results"]}

    assert len(ids("statuses=missed,failed")) == 2
    assert ids("search=5550001") == {missed["id"]}
    assert len(ids("search=contract")) == 1
    assert len(ids("date_from=2026-10-27&date_to=2026-10-28")) == 2
    assert len(ids(f"owner_id={owner_user.pk}")) == 3
    assert owner.get("/api/v1/telephony/calls/?owner_id=x").status_code == 400

    body = {"subject_type": "customer", "subject_id": customer["id"]}
    linked = _ok(owner.patch(f"/api/v1/telephony/calls/{missed['id']}/", body, format="json"))
    assert linked["subject_id"] == customer["id"]
    assert ids(f"subject_type=customer&subject_id={customer['id']}") == {missed["id"]}
    notes = _ok(
        owner.patch(f"/api/v1/telephony/calls/{missed['id']}/", {"notes": "Busy"}, format="json")
    )
    assert notes["notes"] == "Busy" and notes["subject_id"] == customer["id"]
    detached = {"subject_type": None, "subject_id": None}
    unlinked = _ok(owner.patch(f"/api/v1/telephony/calls/{missed['id']}/", detached, format="json"))
    assert unlinked["subject_id"] is None
    half = owner.patch(
        f"/api/v1/telephony/calls/{missed['id']}/", {"subject_type": "customer"}, format="json"
    )
    assert half.status_code == 400


def _dispatch_call_events() -> int:
    handled = 0
    with system_tenant_scope("acme"):
        for row in OutboxMessage.objects.using(ALIAS).filter(event_type="telephony.call_ended"):
            envelope = {
                "event_id": str(row.id),
                "event_type": row.event_type,
                "aggregate_type": row.aggregate_type,
                "aggregate_id": row.aggregate_id,
                "occurred_at": row.occurred_at.isoformat(),
                "payload": row.payload,
                "correlation_id": None,
                "tenant_slug": "acme",
            }
            handled += _dispatch(ALIAS, envelope)
            _dispatch(ALIAS, envelope)  # redelivery is a no-op
    return handled


def test_missed_call_creates_a_call_back_task_and_an_alert(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner_user = make_user("owner", tenant=tenants["acme"])
    owner = client_for(owner_user, "acme")
    customer = _customer(owner, name="Caller", phone="+77015550002")
    _call(owner, status="missed", duration_seconds=0, from_number="+77015550002")
    _call(owner, status="completed")  # answered: no follow-up
    _call(owner, status="missed", duration_seconds=0, from_number="+77015559999")  # unknown

    assert _dispatch_call_events() >= 3
    tasks = ActivityRecord.objects.using(ALIAS).filter(kind="task", task_type="call")
    assert tasks.count() == 1  # only the known caller, once despite redelivery
    task = tasks.get()
    assert str(task.subject_id) == customer["id"]
    assert str(task.assignee_user_id) == str(owner_user.pk)
    alerts = NotificationRecord.objects.using(ALIAS).filter(recipient_user_id=owner_user.pk)
    assert alerts.count() == 2  # both missed calls, the unknown one without a task
