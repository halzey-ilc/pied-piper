"""A dismissed employee's base is handed over by hand; history names who had it before."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)


def _ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.content
    return response.json() if response.content else None


def _hire(director: APIClient, department: str, login: str) -> tuple[dict[str, Any], Any]:
    body = {"first_name": login, "role": "sales_rep", "login": login, "department_id": department}
    created = _ok(director.post("/api/v1/staff/", body, format="json"), 201)
    user = get_user_model().objects.get(id=created["member"]["user_id"])
    user.must_change_password = False
    user.save()
    return created, user


def test_reassignment_history_names_previous_and_new_owner(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    director = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    sales = _ok(director.post("/api/v1/departments/", {"name": "Sales"}, format="json"), 201)["id"]
    leaving, leaving_user = _hire(director, sales, "leaving")
    _, keeper_user = _hire(director, sales, "keeper")
    rep = client_for(leaving_user, "acme")
    stages = [{"name": "New"}, {"name": "Won", "kind": "won"}, {"name": "Lost", "kind": "lost"}]
    _ok(
        director.post(
            "/api/v1/sales/pipelines/",
            {"name": "Main", "is_default": True, "stages": stages},
            format="json",
        ),
        201,
    )

    lead = _ok(
        rep.post("/api/v1/leads/", {"name": "Lead", "phone": "+77011234567"}, format="json"), 201
    )
    customer = _ok(rep.post("/api/v1/customers/", {"name": "Client"}, format="json"), 201)
    deal = _ok(
        rep.post("/api/v1/sales/deals/", {"title": "Deal", "amount": "100.00"}, format="json"), 201
    )

    # Dismissal keeps the base with the employee for manual redistribution
    _ok(director.post(f"/api/v1/staff/{leaving['member']['id']}/deactivate/"))
    keeper = {"owner_user_id": str(keeper_user.id)}
    _ok(director.post(f"/api/v1/leads/{lead['id']}/assign/", keeper, format="json"))
    _ok(director.post(f"/api/v1/customers/{customer['id']}/assign/", keeper, format="json"))
    _ok(director.post(f"/api/v1/sales/deals/{deal['id']}/assign/", keeper, format="json"))

    expected = {
        "from_owner": {"id": str(leaving_user.id), "name": "leaving"},
        "to_owner": {"id": str(keeper_user.id), "name": "keeper"},
    }
    for path in (
        f"/api/v1/leads/{lead['id']}/history/",
        f"/api/v1/customers/{customer['id']}/history/",
        f"/api/v1/sales/deals/{deal['id']}/history/",
    ):
        history = _ok(director.get(path))["results"]
        reassigned = [row for row in history if row["action"].endswith(".reassigned")]
        assert len(reassigned) == 1, path
        assert reassigned[0]["details"] == expected, path

    # The handed-over base is still this person's work history: the account can't be deleted
    profile = _ok(director.get(f"/api/v1/staff/{leaving['member']['id']}/"))
    counts = profile["history"]
    assert (
        counts["transferred_leads"],
        counts["transferred_customers"],
        counts["transferred_deals"],
    ) == (
        1,
        1,
        1,
    )
    assert profile["has_history"] is True
    assert director.delete(f"/api/v1/staff/{leaving['member']['id']}/").status_code == 409


def test_customer_history_is_scoped_like_the_customer(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    director = client_for(make_user("owner", tenant=tenants["acme"]), "acme")
    sales = _ok(director.post("/api/v1/departments/", {"name": "Sales"}, format="json"), 201)["id"]
    _, one = _hire(director, sales, "one")
    _, two = _hire(director, sales, "two")
    customer = _ok(
        client_for(one, "acme").post("/api/v1/customers/", {"name": "C"}, format="json"), 201
    )
    assert (
        client_for(two, "acme").get(f"/api/v1/customers/{customer['id']}/history/").status_code
        == 404
    )
    other_company = client_for(make_user("globex_owner", tenant=tenants["globex"]), "globex")
    assert other_company.get(f"/api/v1/customers/{customer['id']}/history/").status_code == 404
    created = _ok(director.get(f"/api/v1/customers/{customer['id']}/history/"))["results"]
    assert [row["action"] for row in created] == ["customer.created"]
