"""Tenant isolation (acceptance item 6).

A user of one company must not be able to read or modify another company's
data. Checked at several independent layers, not only in a view:
membership gate, physical database separation, router fail-closed and RBAC.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.customers.infrastructure.models import CustomerRecord
from platform_core.domain.tenancy import TenantContextMissingError
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)


@pytest.fixture
def setup(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> dict[str, Any]:
    alice = make_user("alice", tenant=tenants["acme"])  # acme owner
    bob = make_user("bob", tenant=tenants["globex"])  # globex owner
    globex_customer = (
        client_for(bob, "globex")
        .post("/api/v1/customers/", {"name": "Globex Secret Client"}, format="json")
        .json()
    )
    return {"alice": alice, "bob": bob, "globex_customer_id": globex_customer["id"]}


def test_cannot_select_a_tenant_you_are_not_member_of(
    setup: dict[str, Any], client_for: Callable[..., APIClient]
) -> None:
    client = client_for(setup["alice"], "globex")
    for response in (
        client.get("/api/v1/customers/"),
        client.get(f"/api/v1/customers/{setup['globex_customer_id']}/"),
        client.patch(
            f"/api/v1/customers/{setup['globex_customer_id']}/", {"name": "pwned"}, format="json"
        ),
        client.post("/api/v1/customers/", {"name": "planted"}, format="json"),
    ):
        assert response.status_code == 404  # same answer as for a non-existent tenant
        assert response.json()["error"]["code"] == "tenant_not_found"
    assert CustomerRecord.objects.using("tenant_globex").get().name == "Globex Secret Client"
    assert not CustomerRecord.objects.using("tenant_globex").filter(name="planted").exists()


def test_foreign_object_id_is_invisible_from_own_tenant(
    setup: dict[str, Any], client_for: Callable[..., APIClient]
) -> None:
    client = client_for(setup["alice"], "acme")
    customer_id = setup["globex_customer_id"]
    assert client.get(f"/api/v1/customers/{customer_id}/").status_code == 404
    assert (
        client.patch(
            f"/api/v1/customers/{customer_id}/", {"name": "pwned"}, format="json"
        ).status_code
        == 404
    )
    assert client.get("/api/v1/customers/").json() == {"count": 0, "results": []}
    assert CustomerRecord.objects.using("tenant_globex").get().name == "Globex Secret Client"


def test_missing_unknown_or_malformed_tenant_header(
    setup: dict[str, Any], client_for: Callable[..., APIClient]
) -> None:
    for slug in (None, "does-not-exist", "../globex", "GLOBEX", "globex;--"):
        response = client_for(setup["alice"], slug).get("/api/v1/customers/")
        assert response.status_code == 404, slug


def test_deactivated_membership_loses_access(
    setup: dict[str, Any], client_for: Callable[..., APIClient]
) -> None:
    setup["bob"].memberships.update(is_active=False)
    assert client_for(setup["bob"], "globex").get("/api/v1/customers/").status_code == 404


def test_data_is_physically_separated() -> None:
    CustomerRecord.objects.using("tenant_acme").create(
        id=uuid4(), name="Only in acme", owner_user_id=uuid4(), created_at="2026-01-01T00:00:00Z"
    )
    assert CustomerRecord.objects.using("tenant_globex").filter(name="Only in acme").count() == 0


def test_tenant_data_access_without_tenant_context_fails_closed() -> None:
    with pytest.raises(TenantContextMissingError):
        list(CustomerRecord.objects.all())


def test_sales_rep_sees_and_edits_only_own_customers(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    owner = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    rep = client_for(make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP), "acme")
    viewer = client_for(make_user("viewer", tenant=tenants["acme"], role=Role.VIEWER), "acme")

    owners_customer = owner.post("/api/v1/customers/", {"name": "Owner's"}, format="json").json()
    reps_customer = rep.post("/api/v1/customers/", {"name": "Rep's"}, format="json").json()

    assert [c["name"] for c in rep.get("/api/v1/customers/").json()["results"]] == ["Rep's"]
    assert rep.get(f"/api/v1/customers/{owners_customer['id']}/").status_code == 404
    assert (
        rep.patch(
            f"/api/v1/customers/{owners_customer['id']}/", {"name": "x"}, format="json"
        ).status_code
        == 404
    )
    assert (
        rep.patch(
            f"/api/v1/customers/{reps_customer['id']}/", {"name": "Mine"}, format="json"
        ).status_code
        == 200
    )

    assert viewer.get("/api/v1/customers/").json()["count"] == 2
    assert viewer.post("/api/v1/customers/", {"name": "nope"}, format="json").status_code == 403
    assert (
        viewer.patch(
            f"/api/v1/customers/{reps_customer['id']}/", {"name": "x"}, format="json"
        ).status_code
        == 403
    )


def test_jwt_carries_no_tenant_or_database_information(setup: dict[str, Any]) -> None:
    from rest_framework_simplejwt.tokens import AccessToken

    claims = AccessToken.for_user(setup["alice"]).payload
    assert set(claims) <= {"token_type", "exp", "iat", "jti", "sub", "aud", "iss"}
