"""Payments API: invoice lifecycle, payments, link checks, RBAC, isolation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.payments.infrastructure.models import InvoiceRecord
from platform_core.infrastructure.messaging.models import OutboxMessage
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)


def _invoice(client: APIClient, **body: Any) -> Any:
    body.setdefault("amount", "100.00")
    return client.post("/api/v1/payments/invoices/", body, format="json")


def test_create_issue_and_pay_full(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    created = _invoice(client, amount="250.00", currency="USD")
    assert created.status_code == 201, created.content
    invoice_id = created.json()["id"]
    assert created.json()["status"] == "draft"

    assert (
        client.post(
            f"/api/v1/payments/invoices/{invoice_id}/payments/",
            {"amount": "10", "currency": "USD"},
            format="json",
        ).status_code
        == 409
    )
    issued = client.post(f"/api/v1/payments/invoices/{invoice_id}/issue/", {}, format="json")
    assert issued.status_code == 200 and issued.json()["status"] == "issued"

    part = client.post(
        f"/api/v1/payments/invoices/{invoice_id}/payments/",
        {"amount": "100", "currency": "USD"},
        format="json",
    )
    assert part.json()["status"] == "issued" and part.json()["outstanding_amount"] == "150.00"

    rest = client.post(
        f"/api/v1/payments/invoices/{invoice_id}/payments/",
        {"amount": "150", "currency": "USD"},
        format="json",
    )
    assert rest.json()["status"] == "paid"

    events = set(OutboxMessage.objects.using("tenant_acme").values_list("event_type", flat=True))
    assert {
        "payments.invoice_issued",
        "payments.payment_received",
        "payments.invoice_paid",
    } <= events


def test_overpayment_and_void(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    invoice_id = _invoice(client, amount="100.00").json()["id"]
    client.post(f"/api/v1/payments/invoices/{invoice_id}/issue/", {}, format="json")
    assert (
        client.post(
            f"/api/v1/payments/invoices/{invoice_id}/payments/",
            {"amount": "101", "currency": "USD"},
            format="json",
        ).status_code
        == 400
    )
    voided = client.post(
        f"/api/v1/payments/invoices/{invoice_id}/void/",
        {"reason": "cancelled order"},
        format="json",
    )
    assert voided.status_code == 200 and voided.json()["status"] == "void"


def test_linked_customer_must_exist(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    response = _invoice(client, customer_id="00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert InvoiceRecord.objects.using("tenant_acme").count() == 0


def test_rbac_and_isolation(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    rep = client_for(make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP), "acme")
    viewer = client_for(make_user("viewer", tenant=tenants["acme"], role=Role.VIEWER), "acme")

    owners = _invoice(owner).json()["id"]
    reps = _invoice(rep).json()["id"]
    assert [i["id"] for i in rep.get("/api/v1/payments/invoices/").json()["results"]] == [reps]
    assert rep.get(f"/api/v1/payments/invoices/{owners}/").status_code == 404
    assert viewer.get("/api/v1/payments/invoices/").json()["count"] == 2
    assert _invoice(viewer).status_code == 403

    alice = make_user("alice", tenant=tenants["acme"])
    bob = make_user("bob", tenant=tenants["globex"])
    _invoice(client_for(bob, "globex"))
    assert client_for(alice, "globex").get("/api/v1/payments/invoices/").status_code == 404
    assert InvoiceRecord.objects.using("tenant_globex").count() == 1
