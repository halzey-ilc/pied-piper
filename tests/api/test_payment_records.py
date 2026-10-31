"""Payment records API: registration, idempotency, sales metrics, RBAC, isolation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)


def _pipeline(client: APIClient) -> None:
    response = client.post(
        "/api/v1/sales/pipelines/",
        {"name": "P", "is_default": True, "stages": [{"name": "New"}]},
        format="json",
    )
    assert response.status_code == 201, response.content


def _deal(client: APIClient, amount: str) -> str:
    response = client.post("/api/v1/sales/deals/", {"title": "D", "amount": amount}, format="json")
    assert response.status_code == 201, response.content
    deal_id: str = response.json()["id"]
    return deal_id


def _pay(client: APIClient, **body: Any) -> Any:
    body.setdefault("kind", "prepayment")
    body.setdefault("paid_on", "2026-11-10")
    return client.post("/api/v1/payments/", body, format="json")


def test_register_and_list_payment(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    _pipeline(client)
    deal_id = _deal(client, "1000")

    created = _pay(client, deal_id=deal_id, amount="500.00")
    assert created.status_code == 201, created.content
    body = created.json()
    assert body["kind"] == "prepayment" and body["amount"] == "500.00"
    assert body["currency"] == "KGS"  # company default
    assert body["seller_user_id"] == body["owner_user_id"]  # seller defaults to recorder

    listed = client.get(f"/api/v1/payments/?deal_id={deal_id}").json()
    assert listed["count"] == 1 and listed["results"][0]["id"] == body["id"]


def test_payment_registration_is_idempotent_by_reference(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    _pipeline(client)
    deal_id = _deal(client, "1000")

    first = _pay(client, deal_id=deal_id, amount="500.00", reference="chk-001")
    assert first.status_code == 201, first.content
    again = _pay(client, deal_id=deal_id, amount="500.00", reference="chk-001")
    # Same reference returns the first record, not a duplicate.
    assert again.json()["id"] == first.json()["id"]
    assert client.get("/api/v1/payments/").json()["count"] == 1


def test_sales_metrics_counts_turnover_received_and_debt(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    _pipeline(client)
    deal1 = _deal(client, "1000")
    deal2 = _deal(client, "2000")

    _pay(client, deal_id=deal1, amount="500.00", kind="prepayment", paid_on="2026-11-10")
    _pay(
        client,
        deal_id=deal1,
        amount="100.00",
        kind="additional",
        paid_on="2026-11-15",
        is_refund=True,
    )
    _pay(client, deal_id=deal2, amount="2000.00", kind="full", paid_on="2026-11-12")
    # A payment outside the window must not count as a sale in it.
    _pay(client, deal_id=deal1, amount="50.00", kind="additional", paid_on="2026-12-05")

    metrics = client.get("/api/v1/payments/metrics/?paid_from=2026-11-01&paid_to=2026-11-30").json()
    assert metrics["sales_count"] == 2  # both deals' first payment fell in November
    assert metrics["turnover"] == "3000.00"
    assert metrics["received"] == "2400.00"  # Nov: 500 + 2000 - 100 refund
    # Debt uses all-time receipts per sold deal: deal1 500-100+50(Dec)=450, deal2 2000.
    assert metrics["debt"] == "550.00"  # (1000-450) + (2000-2000)
    assert metrics["currency"] == "KGS"


def test_payment_rbac_scope_and_roles(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    rep = client_for(make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP), "acme")
    viewer = client_for(make_user("viewer", tenant=tenants["acme"], role=Role.VIEWER), "acme")
    _pipeline(owner)
    deal_id = _deal(owner, "1000")

    owners_payment = _pay(owner, deal_id=deal_id, amount="100.00").json()["id"]
    reps_payment = _pay(rep, deal_id=deal_id, amount="200.00").json()["id"]

    # A sales rep (OWN scope) sees only their own payment.
    rep_ids = {p["id"] for p in rep.get("/api/v1/payments/").json()["results"]}
    assert rep_ids == {reps_payment}
    # The owner (ALL) sees both.
    assert owner.get("/api/v1/payments/").json()["count"] == 2
    assert owners_payment != reps_payment
    # A viewer cannot register payments.
    assert _pay(viewer, deal_id=deal_id, amount="10.00").status_code == 403
