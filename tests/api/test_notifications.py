"""Notifications: event -> notification path, user-facing API, preferences, per-user scope."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from rest_framework.test import APIClient

from entrypoints.workers.tasks.events import _dispatch
from modules.notifications.infrastructure.models import NotificationRecord
from platform_core.infrastructure.tenancy.services import system_tenant_scope
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)
ALIAS = "tenant_acme"


def _meeting_event(owner_id: str) -> dict[str, Any]:
    return {
        "event_id": str(uuid4()),
        "event_type": "meetings.meeting_scheduled",
        "aggregate_type": "meeting",
        "aggregate_id": "1",
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": {
            "meeting_id": "1",
            "owner_user_id": owner_id,
            "scheduled_start": "2026-11-01T10:00:00Z",
        },
        "correlation_id": None,
        "tenant_slug": "acme",
    }


def test_event_creates_notification_idempotently(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    event = _meeting_event(str(owner.pk))
    with system_tenant_scope("acme"):
        assert _dispatch(ALIAS, event) == 1  # notifications handler
        _dispatch(ALIAS, event)  # redelivery

    assert NotificationRecord.objects.using(ALIAS).filter(recipient_user_id=owner.pk).count() == 1

    client = client_for(owner, "acme")
    listing = client.get("/api/v1/notifications/").json()
    assert listing["count"] == 1 and listing["unread"] == 1
    assert listing["results"][0]["category"] == "meeting"


def test_mark_read_and_mark_all(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    with system_tenant_scope("acme"):
        _dispatch(ALIAS, _meeting_event(str(owner.pk)))
        _dispatch(ALIAS, _meeting_event(str(owner.pk)))
    client = client_for(owner, "acme")
    nid = client.get("/api/v1/notifications/").json()["results"][0]["id"]
    assert (
        client.post(f"/api/v1/notifications/{nid}/read/", {}, format="json").json()["read"] is True
    )
    assert client.get("/api/v1/notifications/unread-count/").json()["unread"] == 1
    assert client.post("/api/v1/notifications/read-all/", {}, format="json").json()["marked"] == 1
    assert client.get("/api/v1/notifications/unread-count/").json()["unread"] == 0


def test_muting_a_category_skips_notifications(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    client = client_for(owner, "acme")
    muted = client.post(
        "/api/v1/notifications/preferences/",
        {"category": "meeting", "enabled": False},
        format="json",
    )
    assert muted.json()["categories"]["meeting"] is False

    with system_tenant_scope("acme"):
        _dispatch(ALIAS, _meeting_event(str(owner.pk)))
    assert NotificationRecord.objects.using(ALIAS).filter(recipient_user_id=owner.pk).count() == 0


def test_notifications_are_per_user(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    alice = make_user("alice", tenant=tenants["acme"])
    bob = make_user("bob", tenant=tenants["acme"])
    with system_tenant_scope("acme"):
        _dispatch(ALIAS, _meeting_event(str(alice.pk)))
    assert client_for(alice, "acme").get("/api/v1/notifications/").json()["count"] == 1
    assert client_for(bob, "acme").get("/api/v1/notifications/").json()["count"] == 0
