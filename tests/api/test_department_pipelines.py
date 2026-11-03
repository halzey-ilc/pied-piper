"""Department sales funnels: a head of sales configures their department's pipeline, its
managers work in it, and deleting a stage moves its deals with history."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)

_STAGES = [
    {"name": "Новый лид", "kind": "open"},
    {"name": "Тёплый", "kind": "open"},
    {"name": "Думает", "kind": "open"},
    {"name": "Успешная сделка", "kind": "won"},
    {"name": "Отказ", "kind": "lost"},
]


def _in_department(user: Any, tenant: TenantFixture, name: str) -> str:
    from modules.organizations.infrastructure.models import Department, Membership

    department, _ = Department.objects.get_or_create(organization=tenant.org, name=name)
    Membership.objects.filter(user=user, organization=tenant.org).update(department=department)
    return str(department.id)


def _company_pipeline(client: APIClient) -> dict[str, Any]:
    response = client.post(
        "/api/v1/sales/pipelines/",
        {
            "name": "Компания",
            "is_default": True,
            "stages": [{"name": "New"}, {"name": "Won", "kind": "won"}],
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    data: dict[str, Any] = response.json()
    return data


def _setup(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> tuple[APIClient, APIClient, APIClient, APIClient, str]:
    tenant = tenants["acme"]
    director = make_user("boss", tenant=tenant, role=Role.DIRECTOR)
    head = make_user("north_head", tenant=tenant, role=Role.HEAD_OF_SALES)
    manager = make_user("north_rep", tenant=tenant, role=Role.SALES_REP)
    outsider_head = make_user("south_head", tenant=tenant, role=Role.HEAD_OF_SALES)
    north = _in_department(head, tenant, "North")
    _in_department(manager, tenant, "North")
    _in_department(outsider_head, tenant, "South")
    return (
        client_for(director, "acme"),
        client_for(head, "acme"),
        client_for(manager, "acme"),
        client_for(outsider_head, "acme"),
        north,
    )


def test_head_of_sales_builds_their_departments_funnel(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    director, head, manager, outsider, north = _setup(make_user, tenants, client_for)
    _company_pipeline(director)

    created = head.post(
        "/api/v1/sales/pipelines/", {"name": "Воронка North", "stages": _STAGES}, format="json"
    )
    assert created.status_code == 201, created.content
    funnel = created.json()
    # Owned by the head's department, and its first pipeline becomes the department default.
    assert funnel["department_id"] == north
    assert funnel["is_default"] is True

    # The manager sees it first; their new deal lands in it.
    listed = manager.get("/api/v1/sales/pipelines/").json()
    assert listed[0]["id"] == funnel["id"]
    deal = manager.post("/api/v1/sales/deals/", {"title": "Окна", "amount": "100"}, format="json")
    assert deal.status_code == 201 and deal.json()["pipeline_id"] == funnel["id"]

    # Managers never change structure; a head of another department neither sees nor edits it.
    stage_url = f"/api/v1/sales/pipelines/{funnel['id']}/stages/"
    assert manager.post(stage_url, {"name": "X"}, format="json").status_code == 403
    assert outsider.post(stage_url, {"name": "X"}, format="json").status_code == 404
    assert funnel["id"] not in [p["id"] for p in outsider.get("/api/v1/sales/pipelines/").json()]
    # A head of sales cannot edit the company-wide pipeline any more.
    company = next(
        p for p in director.get("/api/v1/sales/pipelines/").json() if p["department_id"] is None
    )
    assert (
        head.post(
            f"/api/v1/sales/pipelines/{company['id']}/stages/", {"name": "X"}, format="json"
        ).status_code
        == 403
    )

    # Rename, add, reorder: all allowed for the head.
    stage_ids = [s["id"] for s in funnel["stages"]]
    renamed = head.patch(f"{stage_url}{stage_ids[1]}/", {"name": "Тёплый лид"}, format="json")
    assert renamed.status_code == 200
    added = head.post(stage_url, {"name": "NDZ — не дозвонились", "position": 2}, format="json")
    assert added.status_code == 201
    new_ids = [s["id"] for s in added.json()["stages"]]
    reordered = head.put(
        f"/api/v1/sales/pipelines/{funnel['id']}/stage-order/",
        {"stage_ids": list(reversed(new_ids[:4])) + new_ids[4:]},
        format="json",
    )
    assert reordered.status_code == 200
    counts = head.get(f"/api/v1/sales/pipelines/{funnel['id']}/?counts=true").json()
    first_stage = next(s for s in counts["stages"] if s["id"] == stage_ids[0])
    assert first_stage["open_deals"] == 1 and first_stage["total_deals"] == 1
    assert manager.get(f"/api/v1/sales/pipelines/{funnel['id']}/?counts=true").status_code == 403


def test_deleting_a_stage_moves_its_deals_with_history(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    _, head, manager, _, _ = _setup(make_user, tenants, client_for)
    funnel = head.post(
        "/api/v1/sales/pipelines/", {"name": "Воронка", "stages": _STAGES}, format="json"
    ).json()
    stages = {s["name"]: s["id"] for s in funnel["stages"]}
    deal_id = manager.post(
        "/api/v1/sales/deals/", {"title": "Окна", "amount": "1"}, format="json"
    ).json()["id"]
    url = f"/api/v1/sales/pipelines/{funnel['id']}/stages/{stages['Новый лид']}/"

    # A stage with deals needs a target, of the same kind.
    missing = head.delete(url)
    assert missing.status_code == 400 and missing.json()["error"]["code"] == "move_target_required"
    assert head.delete(f"{url}?move_to_stage_id={stages['Отказ']}").status_code == 400
    assert manager.delete(f"{url}?move_to_stage_id={stages['Думает']}").status_code == 403

    removed = head.delete(f"{url}?move_to_stage_id={stages['Думает']}")
    assert removed.status_code == 200, removed.content
    body = removed.json()
    assert body["moved_deals"] == 1 and body["archived"] is True
    archived = next(s for s in body["pipeline"]["stages"] if s["id"] == stages["Новый лид"])
    assert archived["is_active"] is False

    deal = manager.get(f"/api/v1/sales/deals/{deal_id}/").json()
    assert deal["stage_id"] == stages["Думает"]
    history = manager.get(f"/api/v1/sales/deals/{deal_id}/stage-history/").json()["results"]
    assert history[-1]["from_stage_id"] == stages["Новый лид"]
    assert history[-1]["to_stage_id"] == stages["Думает"]
    assert "удалён" in history[-1]["reason"]

    # A never-used stage is removed outright; the last won stage cannot go.
    unused = head.delete(f"/api/v1/sales/pipelines/{funnel['id']}/stages/{stages['Тёплый']}/")
    assert unused.status_code == 200 and unused.json()["archived"] is False
    assert stages["Тёплый"] not in [s["id"] for s in unused.json()["pipeline"]["stages"]]
    last_won = head.delete(
        f"/api/v1/sales/pipelines/{funnel['id']}/stages/{stages['Успешная сделка']}/"
    )
    assert last_won.status_code == 400


def test_department_deals_move_into_its_funnel_only_on_confirmation(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    director, head, manager, outsider, _north = _setup(make_user, tenants, client_for)
    company = _company_pipeline(director)
    new_stage = next(s["id"] for s in company["stages"] if s["kind"] == "open")
    won_stage = next(s["id"] for s in company["stages"] if s["kind"] == "won")

    def deal(client: APIClient, title: str) -> str:
        response = client.post(
            "/api/v1/sales/deals/", {"title": title, "amount": "10"}, format="json"
        )
        assert response.status_code == 201, response.content
        assert response.json()["pipeline_id"] == company["id"]
        deal_id: str = response.json()["id"]
        return deal_id

    # Deals opened before the department had its own funnel stay in the company pipeline.
    open_deal = deal(manager, "Открытая")
    closed_deal = deal(manager, "Закрытая")
    moved = manager.post(
        f"/api/v1/sales/deals/{closed_deal}/stage/", {"stage_id": won_stage}, format="json"
    )
    assert moved.status_code == 200, moved.content
    foreign_deal = deal(outsider, "Чужая")

    funnel = head.post(
        "/api/v1/sales/pipelines/", {"name": "Воронка North", "stages": _STAGES}, format="json"
    ).json()
    warm = next(s["id"] for s in funnel["stages"] if s["name"] == "Тёплый")
    url = f"/api/v1/sales/pipelines/{funnel['id']}/adopt-deals/"

    # Nothing moved by creating the funnel; the preview counts only the department's open deals.
    preview = head.get(f"{url}?from_pipeline_id={company['id']}")
    assert preview.status_code == 200, preview.content
    assert preview.json()["total"] == 1
    assert {s["stage_id"]: s["open_deals"] for s in preview.json()["stages"]}[new_stage] == 1
    assert director.get(f"/api/v1/sales/deals/{open_deal}/").json()["pipeline_id"] == company["id"]

    # Managers and other departments' heads may not move deals; every stage must be mapped.
    body = {"from_pipeline_id": company["id"], "stage_map": {new_stage: warm}}
    assert manager.post(url, body, format="json").status_code == 403
    assert outsider.post(url, body, format="json").status_code == 404
    unmapped = head.post(url, {"from_pipeline_id": company["id"], "stage_map": {}}, format="json")
    assert unmapped.status_code == 400
    assert unmapped.json()["error"]["code"] == "stage_mapping_required"
    wrong_target = {"from_pipeline_id": company["id"], "stage_map": {new_stage: won_stage}}
    assert head.post(url, wrong_target, format="json").status_code == 400

    adopted = head.post(url, body, format="json")
    assert adopted.status_code == 200, adopted.content
    assert adopted.json() == {"moved": 1, "per_stage": {warm: 1}}

    moved_deal = director.get(f"/api/v1/sales/deals/{open_deal}/").json()
    assert (moved_deal["pipeline_id"], moved_deal["stage_id"]) == (funnel["id"], warm)
    history = director.get(f"/api/v1/sales/deals/{open_deal}/stage-history/").json()["results"]
    assert history[-1]["from_stage_id"] == new_stage and history[-1]["to_stage_id"] == warm
    assert "Перенос в воронку отдела" in history[-1]["reason"]
    # Closed deals and other departments' deals stay where they were.
    assert (
        director.get(f"/api/v1/sales/deals/{closed_deal}/").json()["pipeline_id"] == company["id"]
    )
    assert (
        director.get(f"/api/v1/sales/deals/{foreign_deal}/").json()["pipeline_id"] == company["id"]
    )
