"""Leads API: lifecycle, conversion, RBAC scope and tenant isolation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.customers.infrastructure.models import CustomerRecord
from modules.leads.infrastructure.models import LeadRecord
from platform_core.infrastructure.messaging.models import OutboxMessage
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)


def _capture(client: APIClient, **body: Any) -> dict[str, Any]:
    body.setdefault("name", "Jane Buyer")
    body.setdefault("email", "jane@example.test")
    response = client.post("/api/v1/leads/", body, format="json")
    assert response.status_code == 201, response.content
    data: dict[str, Any] = response.json()
    return data


def test_capture_lead_defaults_to_new_and_writes_event(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    lead = _capture(client, name="Acme Lead", source="website")
    assert lead["status"] == "new"
    assert lead["source"] == "website"
    assert LeadRecord.objects.using("tenant_acme").count() == 1
    events = list(OutboxMessage.objects.using("tenant_acme").values_list("event_type", flat=True))
    assert "leads.lead_captured" in events


def test_full_lifecycle_qualify_then_convert_creates_customer(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    lead = _capture(client, name="Convert Me", email="convert@example.test")
    lead_id = lead["id"]

    qualified = client.post(f"/api/v1/leads/{lead_id}/qualify/", {}, format="json")
    assert qualified.status_code == 200
    assert qualified.json()["status"] == "qualified"

    converted = client.post(f"/api/v1/leads/{lead_id}/convert/", {}, format="json")
    assert converted.status_code == 200, converted.content
    body = converted.json()
    assert body["already_converted"] is False
    assert body["lead"]["status"] == "converted"
    customer_id = body["customer_id"]

    # The customer really exists in the same tenant database, with the lead's data.
    customer = CustomerRecord.objects.using("tenant_acme").get(id=customer_id)
    assert customer.name == "Convert Me"
    assert customer.email == "convert@example.test"
    assert LeadRecord.objects.using("tenant_acme").get(id=lead_id).converted_customer_id is not None

    # Converting again is idempotent: same customer, no duplicate.
    again = client.post(f"/api/v1/leads/{lead_id}/convert/", {}, format="json")
    assert again.status_code == 200
    assert again.json()["already_converted"] is True
    assert again.json()["customer_id"] == customer_id
    assert CustomerRecord.objects.using("tenant_acme").count() == 1


def test_cannot_convert_an_unqualified_lead(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    lead_id = _capture(client)["id"]
    response = client.post(f"/api/v1/leads/{lead_id}/convert/", {}, format="json")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "lead_invalid_state"
    assert CustomerRecord.objects.using("tenant_acme").count() == 0


def test_disqualify_requires_reason_and_can_requalify(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    lead_id = _capture(client)["id"]
    assert client.post(f"/api/v1/leads/{lead_id}/disqualify/", {}, format="json").status_code == 400
    ok = client.post(f"/api/v1/leads/{lead_id}/disqualify/", {"reason": "no budget"}, format="json")
    assert ok.status_code == 200 and ok.json()["status"] == "unqualified"
    requalified = client.post(f"/api/v1/leads/{lead_id}/qualify/", {}, format="json")
    assert requalified.json()["status"] == "qualified"


def test_status_filter_and_paging(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    a = _capture(client, name="Lead A", email="a@example.test")["id"]
    _capture(client, name="Lead B", email="b@example.test")
    client.post(f"/api/v1/leads/{a}/qualify/", {}, format="json")
    assert client.get("/api/v1/leads/").json()["count"] == 2
    qualified = client.get("/api/v1/leads/?status=qualified").json()
    assert qualified["count"] == 1 and qualified["results"][0]["id"] == a


def test_sales_rep_scope_and_roles(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    rep = client_for(make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP), "acme")
    viewer = client_for(make_user("viewer", tenant=tenants["acme"], role=Role.VIEWER), "acme")

    owners_lead = _capture(owner, name="Owner lead", email="o@example.test")["id"]
    reps_lead = _capture(rep, name="Rep lead", email="r@example.test")["id"]

    assert [lead["id"] for lead in rep.get("/api/v1/leads/").json()["results"]] == [reps_lead]
    assert rep.get(f"/api/v1/leads/{owners_lead}/").status_code == 404
    assert viewer.get("/api/v1/leads/").json()["count"] == 2
    assert viewer.post("/api/v1/leads/", {"name": "x", "email": "x@example.test"},
                       format="json").status_code == 403


def test_cross_tenant_isolation(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    alice = make_user("alice", tenant=tenants["acme"])
    bob = make_user("bob", tenant=tenants["globex"])
    bob_lead = _capture(client_for(bob, "globex"), name="Globex lead", email="g@example.test")["id"]

    # Alice is not a member of globex -> 404 for the whole tenant.
    assert client_for(alice, "globex").get("/api/v1/leads/").status_code == 404
    # The globex lead id is invisible from acme.
    assert client_for(alice, "acme").get(f"/api/v1/leads/{bob_lead}/").status_code == 404
    assert LeadRecord.objects.using("tenant_globex").get().name == "Globex lead"
    assert LeadRecord.objects.using("tenant_acme").count() == 0
