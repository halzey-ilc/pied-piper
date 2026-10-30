"""Sales pipeline API: pipeline CRUD, deal lifecycle, win/loss, RBAC, isolation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.sales_pipeline.infrastructure.models import DealRecord, PipelineRecord
from platform_core.infrastructure.messaging.models import OutboxMessage
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)

_STAGES = [
    {"name": "New", "probability": 10},
    {"name": "Negotiation", "probability": 50},
    {"name": "Won soon", "probability": 90},
]


def _pipeline(
    client: APIClient, *, is_default: bool = True, name: str = "Standard"
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/sales/pipelines/",
        {"name": name, "is_default": is_default, "stages": _STAGES},
        format="json",
    )
    assert response.status_code == 201, response.content
    data: dict[str, Any] = response.json()
    return data


def _deal(client: APIClient, **body: Any) -> dict[str, Any]:
    body.setdefault("title", "Acme deal")
    body.setdefault("amount", "1000.00")
    response = client.post("/api/v1/sales/deals/", body, format="json")
    assert response.status_code == 201, response.content
    data: dict[str, Any] = response.json()
    return data


def test_create_pipeline_assigns_positions_and_single_default(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    pipeline = _pipeline(client)
    assert [s["position"] for s in pipeline["stages"]] == [0, 1, 2]
    assert pipeline["is_default"] is True

    # Creating a second default moves the default; only one remains.
    _pipeline(client, name="Enterprise", is_default=True)
    defaults = PipelineRecord.objects.using("tenant_acme").filter(is_default=True).count()
    assert defaults == 1
    assert client.get("/api/v1/sales/pipelines/").json().__len__() == 2


def test_full_deal_lifecycle_open_move_win(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    pipeline = _pipeline(client)
    deal = _deal(client, amount="2500.00", currency="USD")
    deal_id = deal["id"]
    assert deal["status"] == "open"
    assert deal["stage_id"] == pipeline["stages"][0]["id"]

    moved = client.post(
        f"/api/v1/sales/deals/{deal_id}/stage/",
        {"stage_id": pipeline["stages"][1]["id"]},
        format="json",
    )
    assert moved.status_code == 200 and moved.json()["stage_id"] == pipeline["stages"][1]["id"]

    won = client.post(f"/api/v1/sales/deals/{deal_id}/win/", {}, format="json")
    assert won.status_code == 200
    assert won.json()["status"] == "won" and won.json()["closed_at"] is not None

    events = set(OutboxMessage.objects.using("tenant_acme").values_list("event_type", flat=True))
    assert {"sales.deal_created", "sales.deal_stage_changed", "sales.deal_won"} <= events


def test_lose_and_reopen(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    _pipeline(client)
    deal_id = _deal(client)["id"]
    assert client.post(f"/api/v1/sales/deals/{deal_id}/lose/", {}, format="json").status_code == 400
    lost = client.post(f"/api/v1/sales/deals/{deal_id}/lose/", {"reason": "budget"}, format="json")
    assert lost.status_code == 200 and lost.json()["status"] == "lost"
    # A lost deal cannot be edited or moved.
    assert (
        client.patch(f"/api/v1/sales/deals/{deal_id}/", {"title": "x"}, format="json").status_code
        == 409
    )
    reopened = client.post(f"/api/v1/sales/deals/{deal_id}/reopen/", {}, format="json")
    assert reopened.status_code == 200 and reopened.json()["status"] == "open"


def test_open_deal_uses_default_pipeline_when_omitted(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    pipeline = _pipeline(client, is_default=True)
    deal = _deal(client)  # no pipeline_id
    assert deal["pipeline_id"] == pipeline["id"]


def test_cannot_open_deal_without_any_pipeline(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    response = client.post(
        "/api/v1/sales/deals/", {"title": "No pipe", "amount": "1"}, format="json"
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_move_to_foreign_stage_rejected(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    _pipeline(client, name="A", is_default=True)
    other = _pipeline(client, name="B", is_default=False)
    deal_id = _deal(client)["id"]
    response = client.post(
        f"/api/v1/sales/deals/{deal_id}/stage/",
        {"stage_id": other["stages"][0]["id"]},
        format="json",
    )
    assert response.status_code == 404


def test_link_customer_must_exist(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    _pipeline(client)
    created = client.post("/api/v1/customers/", {"name": "Linked Co"}, format="json").json()
    deal = _deal(client, customer_id=created["id"])
    assert deal["customer_id"] == created["id"]
    # A non-existent customer id is rejected.
    bad = client.post(
        "/api/v1/sales/deals/",
        {"title": "x", "amount": "1", "customer_id": "00000000-0000-0000-0000-000000000000"},
        format="json",
    )
    assert bad.status_code == 404


def test_rbac_scope_and_roles(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    rep = client_for(make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP), "acme")
    viewer = client_for(make_user("viewer", tenant=tenants["acme"], role=Role.VIEWER), "acme")
    _pipeline(owner)

    owners_deal = _deal(owner, title="Owner deal")["id"]
    reps_deal = _deal(rep, title="Rep deal")["id"]

    assert [d["id"] for d in rep.get("/api/v1/sales/deals/").json()["results"]] == [reps_deal]
    assert rep.get(f"/api/v1/sales/deals/{owners_deal}/").status_code == 404
    # Reps can view pipelines but not manage them.
    assert rep.get("/api/v1/sales/pipelines/").status_code == 200
    assert (
        rep.post(
            "/api/v1/sales/pipelines/", {"name": "Nope", "stages": _STAGES}, format="json"
        ).status_code
        == 403
    )
    # Viewers see all deals but cannot create.
    assert viewer.get("/api/v1/sales/deals/").json()["count"] == 2
    assert (
        viewer.post(
            "/api/v1/sales/deals/", {"title": "x", "amount": "1"}, format="json"
        ).status_code
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
    _pipeline(bob_client)
    bob_deal = _deal(bob_client, title="Globex deal")["id"]

    assert client_for(alice, "globex").get("/api/v1/sales/deals/").status_code == 404
    assert client_for(alice, "acme").get(f"/api/v1/sales/deals/{bob_deal}/").status_code == 404
    assert DealRecord.objects.using("tenant_globex").get().title == "Globex deal"
    assert DealRecord.objects.using("tenant_acme").count() == 0


# --- stage kinds (open/won/lost) and transitions ---------------------------

_KIND_STAGES = [
    {"name": "New", "probability": 10},  # defaults to kind=open
    {"name": "Closed won", "probability": 100, "kind": "won"},
    {"name": "Closed lost", "probability": 0, "kind": "lost"},
]


def _kind_pipeline(client: APIClient, *, name: str = "Kinds") -> dict[str, Any]:
    response = client.post(
        "/api/v1/sales/pipelines/",
        {"name": name, "is_default": True, "stages": _KIND_STAGES},
        format="json",
    )
    assert response.status_code == 201, response.content
    data: dict[str, Any] = response.json()
    return data


def test_stage_read_exposes_kind_and_active_defaults(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    pipeline = _pipeline(client)  # plain _STAGES, no explicit kind
    for stage in pipeline["stages"]:
        assert stage["kind"] == "open"
        assert stage["is_active"] is True


def test_pipeline_requires_at_least_one_open_stage(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    response = client.post(
        "/api/v1/sales/pipelines/",
        {
            "name": "No open",
            "is_default": True,
            "stages": [
                {"name": "Won", "kind": "won"},
                {"name": "Lost", "kind": "lost"},
            ],
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_transition_to_won_stage_closes_deal_as_won(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    pipeline = _kind_pipeline(client)
    won_stage = next(s for s in pipeline["stages"] if s["kind"] == "won")
    deal_id = _deal(client, amount="4000.00")["id"]

    response = client.post(
        f"/api/v1/sales/deals/{deal_id}/stage/",
        {"stage_id": won_stage["id"]},
        format="json",
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["status"] == "won"
    assert body["stage_id"] == won_stage["id"]
    assert body["closed_at"] is not None

    events = set(OutboxMessage.objects.using("tenant_acme").values_list("event_type", flat=True))
    assert "sales.deal_won" in events


def test_transition_to_lost_stage_requires_reason(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    pipeline = _kind_pipeline(client)
    lost_stage = next(s for s in pipeline["stages"] if s["kind"] == "lost")
    deal_id = _deal(client)["id"]

    without_reason = client.post(
        f"/api/v1/sales/deals/{deal_id}/stage/",
        {"stage_id": lost_stage["id"]},
        format="json",
    )
    assert without_reason.status_code == 400

    with_reason = client.post(
        f"/api/v1/sales/deals/{deal_id}/stage/",
        {"stage_id": lost_stage["id"], "reason": "budget cut"},
        format="json",
    )
    assert with_reason.status_code == 200, with_reason.content
    body = with_reason.json()
    assert body["status"] == "lost"
    assert body["stage_id"] == lost_stage["id"]
    assert body["lost_reason"] == "budget cut"
