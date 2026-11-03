"""Imports: CSV preview with duplicate detection, commit through the services, audit."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.audit.infrastructure.models import AuditRecord
from modules.imports.domain.rows import ImportEntity, normalize_phone, parse_rows
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)

CSV = (
    "Имя;Телефон;E-mail;Источник\n"
    "Айгуль;+996 555 123-456;;сайт\n"
    "Бакыт;;bakyt@example.test;\n"
    "Повтор;+996555123456;;\n"
    "Без контактов;;;\n"
    "Старый клиент;+996700000001;;\n"
    "Плохой телефон;0555123456;;\n"
)


def test_parse_and_normalize() -> None:
    assert normalize_phone("+996 (555) 12-34-56") == ("+996555123456", None)
    assert normalize_phone("00996555123456") == ("+996555123456", None)
    assert normalize_phone("0555123456")[1] is not None
    rows = parse_rows("name,phone\nA,+996555000111\n", ImportEntity.CUSTOMERS)
    assert rows[0].phone == "+996555000111" and rows[0].line == 2


def test_preview_then_import_leads(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    director = client_for(make_user("dir", tenant=tenants["acme"], role=Role.DIRECTOR), "acme")
    existing = director.post(
        "/api/v1/customers/", {"name": "Старый", "phone": "+996700000001"}, format="json"
    )
    assert existing.status_code == 201, existing.content

    preview = director.post(
        "/api/v1/imports/preview/", {"entity": "leads", "content": CSV}, format="json"
    )
    assert preview.status_code == 200, preview.content
    body = preview.json()
    by_line = {row["line"]: row for row in body["rows"]}
    assert (body["new"], body["duplicates"], body["invalid"]) == (2, 2, 2)
    assert by_line[2]["phone"] == "+996555123456" and by_line[2]["status"] == "new"
    assert by_line[4]["status"] == "duplicate" and by_line[4]["duplicate_line"] == 2
    assert by_line[6]["duplicate_of"]["kind"] == "customer"
    assert by_line[5]["status"] == by_line[7]["status"] == "invalid"
    # The preview saves nothing.
    assert director.get("/api/v1/leads/").json()["count"] == 0

    job = director.post(
        "/api/v1/imports/",
        {"entity": "leads", "filename": "leads.csv", "content": CSV},
        format="json",
    )
    assert job.status_code == 201, job.content
    result = job.json()
    assert (result["created"], result["duplicates"], result["invalid"], result["failed"]) == (
        2,
        2,
        2,
        0,
    )
    leads = director.get("/api/v1/leads/").json()["results"]
    assert {lead["name"] for lead in leads} == {"Айгуль", "Бакыт"}
    assert {lead["source"] for lead in leads} == {"website", "import"}
    # Importing the same file again creates nothing: every row is now a duplicate.
    again = director.post(
        "/api/v1/imports/", {"entity": "leads", "content": CSV}, format="json"
    ).json()
    assert again["created"] == 0
    assert director.get("/api/v1/imports/").json()["count"] == 2
    assert AuditRecord.objects.using("tenant_acme").filter(action="import.completed").count() == 2


def test_import_customers_to_another_owner_and_rules(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    director = client_for(make_user("dir", tenant=tenants["acme"], role=Role.DIRECTOR), "acme")
    rep = make_user("rep", tenant=tenants["acme"], role=Role.SALES_REP)
    csv_text = "Name,Email\nAcme LLC,info@acme.test\n"
    job = director.post(
        "/api/v1/imports/",
        {"entity": "customers", "content": csv_text, "owner_user_id": str(rep.id)},
        format="json",
    )
    assert job.status_code == 201, job.content
    assert job.json()["created"] == 1
    customers = director.get("/api/v1/customers/?archived=all").json()["results"]
    assert customers[0]["owner_user_id"] == str(rep.id)
    # A file without a name column is rejected as a whole.
    bad = director.post(
        "/api/v1/imports/preview/", {"entity": "customers", "content": "a,b\n1,2\n"}, format="json"
    )
    assert bad.status_code == 400
    # Another company sees nothing of it.
    other = client_for(make_user("other", tenant=tenants["globex"], role=Role.DIRECTOR), "globex")
    assert other.get("/api/v1/imports/").json()["count"] == 0
