"""Telephony API: logging, click-to-call lifecycle, RBAC, isolation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.telephony.infrastructure.models import CallRecord
from platform_core.infrastructure.messaging.models import OutboxMessage
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)


def _log(client: APIClient, **body: Any) -> Any:
    body.setdefault("direction", "inbound")
    body.setdefault("from_number", "+77011112233")
    body.setdefault("to_number", "+77019998877")
    body.setdefault("status", "completed")
    body.setdefault("started_at", "2026-10-28T09:00:00Z")
    body.setdefault("duration_seconds", 120)
    return client.post("/api/v1/telephony/calls/", body, format="json")


def test_log_call(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    response = _log(client, recording_url="https://rec.example.test/a")
    assert response.status_code == 201, response.content
    assert response.json()["status"] == "completed"
    assert (
        OutboxMessage.objects.using("tenant_acme")
        .filter(event_type="telephony.call_ended")
        .exists()
    )


def test_invalid_number_rejected(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    assert _log(client, from_number="12345").status_code == 400


def test_click_to_call_lifecycle(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    requested = client.post(
        "/api/v1/telephony/calls/request/",
        {"from_number": "+77011112233", "to_number": "+77019998877"},
        format="json",
    )
    assert requested.status_code == 201
    body = requested.json()
    assert body["status"] == "requested" and body["external_id"].startswith("stub-")
    call_id = body["id"]

    client.post(f"/api/v1/telephony/calls/{call_id}/advance/", {"status": "ringing"}, format="json")
    inprog = client.post(
        f"/api/v1/telephony/calls/{call_id}/advance/", {"status": "in_progress"}, format="json"
    )
    assert inprog.json()["status"] == "in_progress"
    ended = client.post(
        f"/api/v1/telephony/calls/{call_id}/end/",
        {"status": "completed", "duration_seconds": 75},
        format="json",
    )
    assert ended.json()["status"] == "completed" and ended.json()["duration_seconds"] == 75


def test_rbac_and_isolation(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    rep = client_for(make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP), "acme")
    viewer = client_for(make_user("viewer", tenant=tenants["acme"], role=Role.VIEWER), "acme")

    owners = _log(owner).json()["id"]
    reps = _log(rep).json()["id"]
    assert [c["id"] for c in rep.get("/api/v1/telephony/calls/").json()["results"]] == [reps]
    assert rep.get(f"/api/v1/telephony/calls/{owners}/").status_code == 404
    assert viewer.get("/api/v1/telephony/calls/").json()["count"] == 2
    assert _log(viewer).status_code == 403

    alice = make_user("alice", tenant=tenants["acme"])
    bob = make_user("bob", tenant=tenants["globex"])
    _log(client_for(bob, "globex"))
    assert client_for(alice, "globex").get("/api/v1/telephony/calls/").status_code == 404
    assert CallRecord.objects.using("tenant_globex").count() == 1
