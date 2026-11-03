"""Company reference lists: meeting cancel/no-show reasons and lead source labels."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)


def test_free_list_add_rename_reorder_and_access(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    tenant = tenants["acme"]
    director = client_for(make_user("boss", tenant=tenant, role=Role.DIRECTOR), "acme")
    head = client_for(make_user("head", tenant=tenant, role=Role.HEAD_OF_SALES), "acme")
    rep = client_for(make_user("rep", tenant=tenant, role=Role.SALES_REP), "acme")
    url = "/api/v1/reference-lists/meeting_cancel/"

    empty = rep.get(url).json()
    assert empty == {"key": "meeting_cancel", "fixed": False, "editable": False, "items": []}
    assert director.get(url).json()["editable"] is True

    first = director.post(url, {"label": "Клиент заболел"}, format="json")
    assert first.status_code == 201, first.content
    second = director.post(url, {"label": "Перенёс сам"}, format="json").json()
    assert director.post(url, {"label": "клиент заболел"}, format="json").status_code == 409
    assert head.post(url, {"label": "Другое"}, format="json").status_code == 403
    assert rep.post(url, {"label": "Другое"}, format="json").status_code == 403

    first_id = first.json()["id"]
    renamed = director.patch(
        f"{url}items/{first_id}/", {"label": "Болезнь", "is_active": False}, format="json"
    )
    assert renamed.status_code == 200, renamed.content
    assert renamed.json()["label"] == "Болезнь" and renamed.json()["is_active"] is False
    assert [i["label"] for i in rep.get(f"{url}?active=true").json()["items"]] == ["Перенёс сам"]

    order = director.put(f"{url}order/", {"ids": [second["id"], first_id]}, format="json")
    assert order.status_code == 200, order.content
    assert [i["id"] for i in order.json()["items"]] == [second["id"], first_id]
    assert director.put(f"{url}order/", {"ids": [first_id]}, format="json").status_code == 400
    assert director.get("/api/v1/reference-lists/nope/").status_code == 400


def test_lead_sources_keep_codes(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    tenant = tenants["acme"]
    director = client_for(make_user("boss", tenant=tenant, role=Role.DIRECTOR), "acme")
    rep = client_for(make_user("rep", tenant=tenant, role=Role.SALES_REP), "acme")
    url = "/api/v1/reference-lists/lead_source/"

    sources = rep.get(url).json()
    assert sources["fixed"] is True
    codes = [i["code"] for i in sources["items"]]
    assert "website" in codes and "import" in codes and len(codes) == 8
    # Codes come from the lead model: they can be renamed and switched off, not added.
    assert director.post(url, {"label": "TikTok"}, format="json").status_code == 400
    renamed = director.patch(f"{url}items/website/", {"label": "Сайт компании"}, format="json")
    assert renamed.status_code == 200, renamed.content
    assert renamed.json()["code"] == "website"
    off = director.patch(f"{url}items/import/", {"is_active": False}, format="json")
    assert off.status_code == 200
    active = rep.get(f"{url}?active=true").json()["items"]
    assert "import" not in [i["code"] for i in active]
    assert next(i for i in active if i["code"] == "website")["label"] == "Сайт компании"
    assert director.patch(f"{url}items/fax/", {"is_active": False}, format="json").status_code in (
        400,
        404,
    )
