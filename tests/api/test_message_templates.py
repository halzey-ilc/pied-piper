"""Message templates, channel status, integrations status — honest about providers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from pydantic import SecretStr
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)


def test_templates_crud_and_rights(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    director = client_for(make_user("dir", tenant=tenants["acme"], role=Role.DIRECTOR), "acme")
    rep = client_for(make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP), "acme")
    created = director.post(
        "/api/v1/communications/templates/",
        {"title": "Приветствие", "body": "Здравствуйте! Это Axlon."},
        format="json",
    )
    assert created.status_code == 201, created.content
    template_id = created.json()["id"]
    assert rep.get("/api/v1/communications/templates/").json()["count"] == 1
    assert (
        rep.post(
            "/api/v1/communications/templates/", {"title": "x", "body": "y"}, format="json"
        ).status_code
        == 403
    )
    edited = director.patch(
        f"/api/v1/communications/templates/{template_id}/", {"body": "Добрый день!"}, format="json"
    )
    assert edited.status_code == 200 and edited.json()["body"] == "Добрый день!"
    assert (
        director.post(
            "/api/v1/communications/templates/", {"title": "", "body": "y"}, format="json"
        ).status_code
        == 400
    )
    assert director.delete(f"/api/v1/communications/templates/{template_id}/").status_code == 204
    assert director.get("/api/v1/communications/templates/").json()["count"] == 0


def test_channels_report_no_outbound_and_customer_filter(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    director = client_for(make_user("dir", tenant=tenants["acme"], role=Role.DIRECTOR), "acme")
    channels = director.get("/api/v1/communications/channels/").json()["results"]
    assert channels and all(c["can_send"] is False for c in channels)
    listed = director.get(
        "/api/v1/communications/conversations/?customer_id=00000000-0000-0000-0000-000000000001"
    )
    assert listed.status_code == 200 and listed.json()["count"] == 0
    assert director.get("/api/v1/communications/conversations/?customer_id=bad").status_code == 400


def test_integrations_status_and_check(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
    settings: Any,
) -> None:
    settings.INTEGRATIONS = {
        **settings.INTEGRATIONS,
        "SECRETS": {
            "whatsapp": None,
            "instagram": None,
            "telegram": SecretStr("tg-secret"),
            "telephony": None,
        },
    }
    director = client_for(make_user("dir", tenant=tenants["acme"], role=Role.DIRECTOR), "acme")
    rep = client_for(make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP), "acme")
    statuses = {s["provider"]: s for s in director.get("/api/v1/integrations/").json()["results"]}
    assert statuses["telegram"]["inbound_configured"] is True
    assert statuses["whatsapp"]["inbound_configured"] is False
    assert all(s["outbound_connected"] is False for s in statuses.values())
    assert statuses["telegram"]["webhook_path"] == "/api/v1/webhooks/telegram/acme/"
    check = director.post("/api/v1/integrations/telegram/check/").json()
    items = {i["name"]: i["ok"] for i in check["items"]}
    assert items == {
        "inbound_secret": True,
        "event_store": True,
        "outbound": False,
        "provider_reachability": False,
    }
    assert director.post("/api/v1/integrations/whatsapp/check/").json()["ok"] is False
    assert director.post("/api/v1/integrations/fax/check/").status_code == 404
    assert rep.get("/api/v1/integrations/").status_code == 403
