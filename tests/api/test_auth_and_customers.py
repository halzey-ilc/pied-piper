"""Authentication (JWT, Argon2) and the customer API happy paths."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.audit.infrastructure.models import AuditRecord
from modules.customers.infrastructure.models import CustomerRecord
from platform_core.infrastructure.messaging.models import OutboxMessage
from tests.conftest import ALL_DATABASES, PASSWORD, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)


def test_passwords_are_hashed_with_argon2(make_user: Callable[..., Any]) -> None:
    user = make_user("alice")
    assert user.password.startswith("argon2$")


def test_token_obtain_and_me(
    make_user: Callable[..., Any], tenants: dict[str, TenantFixture]
) -> None:
    make_user("alice", tenant=tenants["acme"])
    client = APIClient()
    response = client.post(
        "/api/v1/auth/token/", {"username": "alice", "password": PASSWORD}, format="json"
    )
    assert response.status_code == 200
    access = response.json()["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    me = client.get("/api/v1/me/")
    assert me.status_code == 200
    assert me.json()["memberships"] == [{"tenant": "acme", "role": "owner"}]


def test_wrong_password_is_rejected(make_user: Callable[..., Any]) -> None:
    make_user("alice")
    response = APIClient().post(
        "/api/v1/auth/token/", {"username": "alice", "password": "nope"}, format="json"
    )
    assert response.status_code == 401


def test_create_list_update_customer_writes_outbox_and_audit(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = make_user("owner", tenant=tenants["acme"])
    client = client_for(owner, "acme")

    created = client.post(
        "/api/v1/customers/", {"name": "Kazakh Trade", "email": "kt@example.test"}, format="json"
    )
    assert created.status_code == 201, created.content
    customer_id = created.json()["id"]

    listed = client.get("/api/v1/customers/")
    assert listed.json()["count"] == 1

    updated = client.patch(
        f"/api/v1/customers/{customer_id}/", {"name": "KT LLP", "version": 1}, format="json"
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2

    stale = client.patch(
        f"/api/v1/customers/{customer_id}/", {"name": "Old", "version": 1}, format="json"
    )
    assert stale.status_code == 409

    # Everything was written to acme's own database, and nothing to the control plane.
    assert CustomerRecord.objects.using("tenant_acme").count() == 1
    events = list(OutboxMessage.objects.using("tenant_acme").values_list("event_type", flat=True))
    assert sorted(events) == ["customers.customer_created", "customers.customer_updated"]
    assert AuditRecord.objects.using("tenant_acme").filter(
        action="customer.updated"
    ).get().changed_fields == ["name"]
    assert OutboxMessage.objects.using("default").count() == 0


def test_validation_errors_are_400_with_details(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    client = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    response = client.post("/api/v1/customers/", {"name": "x", "phone": "12345"}, format="json")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"

    duplicate = {"name": "A", "email": "dup@example.test"}
    assert client.post("/api/v1/customers/", duplicate, format="json").status_code == 201
    assert client.post("/api/v1/customers/", duplicate, format="json").status_code == 409
