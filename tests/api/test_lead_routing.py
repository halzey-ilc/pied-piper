"""Automatic lead distribution: settings permissions, capture in turn, audit, fallbacks."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.leads.infrastructure.models import LeadRecord
from modules.notifications.infrastructure.models import NotificationRecord
from modules.planning.infrastructure.models import AbsenceRecord, WorkSessionRecord
from platform_core.infrastructure.messaging.models import OutboxMessage
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)

ROUTING = "/api/v1/leads/routing/"
ALIAS = "tenant_acme"


def _in_department(user: Any, tenant: TenantFixture, name: str) -> str:
    from modules.organizations.infrastructure.models import Department, Membership

    department, _ = Department.objects.get_or_create(organization=tenant.org, name=name)
    Membership.objects.filter(user=user, organization=tenant.org).update(department=department)
    return str(department.id)


def _on_shift(user: Any) -> None:
    now = datetime.now(UTC)
    WorkSessionRecord.objects.using(ALIAS).create(
        id=uuid4(), user_id=user.id, started_at=now, status="open", created_at=now
    )


def _absent_today(user: Any) -> None:
    today = datetime.now(UTC).date()
    AbsenceRecord.objects.using(ALIAS).create(
        id=uuid4(),
        user_id=user.id,
        kind="vacation",
        start_date=today - timedelta(days=2),
        end_date=today + timedelta(days=2),
        created_by=user.id,
        created_at=datetime.now(UTC),
    )


def _capture(client: APIClient, name: str) -> dict[str, Any]:
    body = {"name": name, "phone": f"+99655500{abs(hash(name)) % 10000:04d}"}
    response = client.post("/api/v1/leads/", body, format="json")
    assert response.status_code == 201, response.content
    data: dict[str, Any] = response.json()
    return data


@pytest.fixture
def team(make_user: Callable[..., Any], tenants: dict[str, TenantFixture]) -> dict[str, Any]:
    acme = tenants["acme"]
    head = make_user("head", tenant=acme, role=Role.HEAD_OF_SALES)
    north = _in_department(head, acme, "North")
    reps = [make_user(f"rep{i}", tenant=acme, role=Role.SALES_REP) for i in range(3)]
    for rep in reps:
        _in_department(rep, acme, "North")
    other_head = make_user("south_head", tenant=acme, role=Role.HEAD_OF_SALES)
    south = _in_department(other_head, acme, "South")
    director = make_user("boss", tenant=acme, role=Role.DIRECTOR)
    return {
        "head": head,
        "north": north,
        "south": south,
        "reps": sorted(reps, key=lambda u: str(u.id)),  # the rotation order
        "director": director,
    }


def test_settings_permissions_and_default_off(
    team: dict[str, Any], client_for: Callable[..., APIClient]
) -> None:
    head = client_for(team["head"], "acme")
    body = head.get(ROUTING).json()
    assert body["department_id"] == team["north"]
    assert body["enabled"] is False and body["is_default"] is True
    assert len(body["candidates"]) == 3 and not any(c["eligible"] for c in body["candidates"])

    # A head of sales: own department only, and cannot pick the company intake department.
    assert head.get(f"{ROUTING}?department_id={team['south']}").status_code == 403
    assert (
        head.put(ROUTING, {"enabled": True, "is_company_intake": True}, format="json").status_code
        == 403
    )
    stranger = {"enabled": True, "member_ids": [str(team["director"].id)]}
    assert head.put(ROUTING, stranger, format="json").status_code == 400

    # A manager cannot see or change routing.
    rep = client_for(team["reps"][0], "acme")
    assert rep.get(ROUTING).status_code == 403
    assert rep.put(ROUTING, {"enabled": True}, format="json").status_code == 403

    # The director: any department, which must be named.
    boss = client_for(team["director"], "acme")
    assert boss.get(ROUTING).status_code == 400
    assert boss.get(f"{ROUTING}?department_id={uuid4()}").status_code == 404
    saved = boss.put(
        f"{ROUTING}?department_id={team['south']}",
        {"enabled": True, "is_company_intake": True},
        format="json",
    )
    assert saved.status_code == 200, saved.content
    assert saved.json()["is_company_intake"] is True and saved.json()["is_default"] is False


def test_capture_by_head_goes_in_turn_to_managers_on_shift(
    team: dict[str, Any], client_for: Callable[..., APIClient]
) -> None:
    first, second, third = team["reps"]
    _on_shift(first)
    _on_shift(second)
    _on_shift(third)
    _absent_today(second)
    head = client_for(team["head"], "acme")
    assert head.put(ROUTING, {"enabled": True}, format="json").status_code == 200

    leads = [_capture(head, f"Lead {i}") for i in range(3)]
    assert [lead["owner_user_id"] for lead in leads] == [
        str(first.id),
        str(third.id),
        str(first.id),
    ]
    assert all(lead["team_id"] == team["north"] for lead in leads)

    history = head.get(f"/api/v1/leads/{leads[0]['id']}/history/").json()["results"]
    auto = [row for row in history if row["action"] == "lead.auto_assigned"]
    assert len(auto) == 1
    assert auto[0]["details"]["from_owner"]["id"] == str(team["head"].id)
    assert auto[0]["details"]["to_owner"]["id"] == str(first.id)
    # The «new lead» notification handler tells the event's owner: the assignee.
    event = OutboxMessage.objects.using(ALIAS).get(
        event_type="leads.lead_captured", aggregate_id=leads[0]["id"]
    )
    assert event.payload["owner_user_id"] == str(first.id)

    # A lead a manager captures stays theirs.
    own = _capture(client_for(third, "acme"), "Mine")
    assert own["owner_user_id"] == str(third.id)


def test_nobody_on_shift_keeps_lead_with_creator_and_tells_the_head(
    team: dict[str, Any], client_for: Callable[..., APIClient]
) -> None:
    head = client_for(team["head"], "acme")
    assert head.put(ROUTING, {"enabled": True}, format="json").status_code == 200
    lead = _capture(head, "Night lead")
    assert lead["owner_user_id"] == str(team["head"].id)
    alert = NotificationRecord.objects.using(ALIAS).get(
        recipient_user_id=team["head"].id, category="system"
    )
    assert "Night lead" in alert.body


def test_routing_off_or_turned_off_leaves_leads_with_creator(
    team: dict[str, Any], client_for: Callable[..., APIClient]
) -> None:
    _on_shift(team["reps"][0])
    head = client_for(team["head"], "acme")
    assert _capture(head, "Before")["owner_user_id"] == str(team["head"].id)
    head.put(ROUTING, {"enabled": True}, format="json")
    assert _capture(head, "During")["owner_user_id"] == str(team["reps"][0].id)
    head.put(ROUTING, {"enabled": False}, format="json")
    assert _capture(head, "After")["owner_user_id"] == str(team["head"].id)
    assert LeadRecord.objects.using(ALIAS).count() == 3


def test_director_without_department_uses_the_intake_department(
    team: dict[str, Any], client_for: Callable[..., APIClient]
) -> None:
    rep = team["reps"][1]
    _on_shift(rep)
    boss = client_for(team["director"], "acme")
    saved = boss.put(
        f"{ROUTING}?department_id={team['north']}",
        {"enabled": True, "member_ids": [str(rep.id)], "is_company_intake": True},
        format="json",
    )
    assert saved.status_code == 200, saved.content
    lead = _capture(boss, "From the site")
    assert (lead["owner_user_id"], lead["team_id"]) == (str(rep.id), team["north"])
