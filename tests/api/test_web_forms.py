"""Website lead forms: key management permissions, the public endpoint, tenant isolation,
routing, revocation, validation, honeypot, duplicates, throttling, CORS, subscription."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.activities.infrastructure.models import ActivityRecord
from modules.audit.infrastructure.models import AuditRecord
from modules.leads.infrastructure.models import LeadRecord
from modules.organizations.infrastructure.models import Department, Membership
from modules.planning.infrastructure.models import WorkSessionRecord
from modules.subscriptions.infrastructure.models import SubscriptionRecord
from modules.web_forms.infrastructure.models import WebFormKeyRecord
from modules.web_forms.presentation.throttles import WebFormIpThrottle, WebFormTokenThrottle
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)

KEYS = "/api/v1/web-forms/keys/"
ROUTING = "/api/v1/leads/routing/"
ACME = "tenant_acme"
GLOBEX = "tenant_globex"


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    cache.clear()  # throttles and the duplicate window live in the cache


def _in_department(user: Any, tenant: TenantFixture, name: str) -> str:
    department, _ = Department.objects.get_or_create(organization=tenant.org, name=name)
    Membership.objects.filter(user=user, organization=tenant.org).update(department=department)
    return str(department.id)


def _on_shift(user: Any) -> None:
    now = datetime.now(UTC)
    WorkSessionRecord.objects.using(ACME).create(
        id=uuid.uuid4(), user_id=user.id, started_at=now, status="open", created_at=now
    )


def _public(token: str) -> str:
    return f"/api/v1/public/leads/{token}/"


@pytest.fixture
def team(make_user: Callable[..., Any], tenants: dict[str, TenantFixture]) -> dict[str, Any]:
    acme = tenants["acme"]
    head = make_user("head", tenant=acme, role=Role.HEAD_OF_SALES)
    north = _in_department(head, acme, "North")
    rep = make_user("rep", tenant=acme, role=Role.SALES_REP)
    _in_department(rep, acme, "North")
    south_head = make_user("south_head", tenant=acme, role=Role.HEAD_OF_SALES)
    south = _in_department(south_head, acme, "South")
    director = make_user("boss", tenant=acme, role=Role.DIRECTOR)
    globex_owner = make_user("globex_owner", tenant=tenants["globex"], role=Role.OWNER)
    return {
        "head": head,
        "north": north,
        "rep": rep,
        "south_head": south_head,
        "south": south,
        "director": director,
        "globex_owner": globex_owner,
    }


def _create_key(client: APIClient, name: str = "Сайт", **extra: Any) -> dict[str, Any]:
    response = client.post(KEYS, {"name": name, **extra}, format="json")
    assert response.status_code == 201, response.content
    body: dict[str, Any] = response.json()
    return body


def test_key_management_permissions(
    team: dict[str, Any], client_for: Callable[..., APIClient]
) -> None:
    head = client_for(team["head"], "acme")
    key = _create_key(head)
    assert key["department_id"] == team["north"] and key["department_name"] == "North"
    assert key["is_active"] is True and key["leads_count"] == 0
    assert key["endpoint_path"] == _public(key["token"]) and len(key["token"]) == 32
    assert key["created_by"] == str(team["head"].id)

    # A head of sales: own department only.
    other = head.post(KEYS, {"name": "X", "department_id": team["south"]}, format="json")
    assert other.status_code == 403
    assert head.get(f"{KEYS}?department_id={team['south']}").status_code == 403

    # A sales manager: nothing.
    rep = client_for(team["rep"], "acme")
    assert rep.get(KEYS).status_code == 403
    assert rep.post(KEYS, {"name": "X"}, format="json").status_code == 403
    assert rep.post(f"{KEYS}{key['id']}/revoke/").status_code == 403

    # Another department's head neither sees nor revokes the key.
    south = client_for(team["south_head"], "acme")
    assert south.get(KEYS).json()["count"] == 0
    assert south.post(f"{KEYS}{key['id']}/revoke/").status_code == 404

    # The director: any department, which must be named and exist.
    boss = client_for(team["director"], "acme")
    assert boss.post(KEYS, {"name": "X"}, format="json").status_code == 400
    missing = boss.post(KEYS, {"name": "X", "department_id": str(uuid.uuid4())}, format="json")
    assert missing.status_code == 404
    south_key = _create_key(boss, "Лендинг", department_id=team["south"])
    assert south_key["department_id"] == team["south"]
    assert boss.get(KEYS).json()["count"] == 2
    assert boss.get(f"{KEYS}?department_id={team['north']}").json()["count"] == 1
    assert head.get(KEYS).json()["count"] == 1

    # Another company does not see acme's keys, nor revoke them.
    globex = client_for(team["globex_owner"], "globex")
    assert globex.get(KEYS).json()["count"] == 0
    assert globex.post(f"{KEYS}{key['id']}/revoke/").status_code == 404

    # Revocation is audited in the company's database and idempotent.
    revoked = head.post(f"{KEYS}{key['id']}/revoke/")
    assert revoked.status_code == 200 and revoked.json()["is_active"] is False
    assert head.post(f"{KEYS}{key['id']}/revoke/").status_code == 200
    actions = set(
        AuditRecord.objects.using(ACME)
        .filter(resource_type="web_form_key")
        .values_list("action", flat=True)
    )
    assert actions == {"web_form_key.created", "web_form_key.revoked"}

    # Name validation.
    assert head.post(KEYS, {"name": "   "}, format="json").status_code == 400
    assert head.post(KEYS, {"name": "x" * 101}, format="json").status_code == 400


def test_public_submit_creates_a_routed_lead_in_the_right_tenant_only(
    team: dict[str, Any], client_for: Callable[..., APIClient]
) -> None:
    head = client_for(team["head"], "acme")
    assert head.put(ROUTING, {"enabled": True}, format="json").status_code == 200
    _on_shift(team["rep"])
    key = _create_key(head)

    response = APIClient().post(
        _public(key["token"]),
        {
            "name": "Айгуль",
            "phone": "+996 555 123-456",
            "comment": "Перезвоните после обеда",
            "page_url": "https://example.test/price",
            "utm_source": "google",
            "utm_campaign": "spring",
        },
        format="json",
    )
    assert response.status_code == 202, response.content
    assert response.json() == {"status": "accepted"}

    lead = LeadRecord.objects.using(ACME).get()
    assert (lead.name, lead.phone, lead.source) == ("Айгуль", "+996555123456", "website")
    # Routing applies exactly as for a lead captured by the head: the manager on shift.
    assert lead.owner_user_id == team["rep"].id and str(lead.team_id) == team["north"]
    assert not LeadRecord.objects.using(GLOBEX).exists()

    note = ActivityRecord.objects.using(ACME).get(subject_id=lead.id, kind="note")
    assert note.body is not None
    assert "Перезвоните после обеда" in note.body and "utm_source: google" in note.body
    assert "https://example.test/price" in note.body

    actions = set(
        AuditRecord.objects.using(ACME)
        .filter(resource_type="lead", resource_id=str(lead.id))
        .values_list("action", flat=True)
    )
    assert {"lead.captured", "lead.auto_assigned", "web_form.lead_received"} <= actions

    listed = head.get(KEYS).json()["results"][0]
    assert listed["leads_count"] == 1 and listed["last_used_at"] is not None


def test_nobody_eligible_keeps_the_lead_with_the_key_owner(
    team: dict[str, Any], client_for: Callable[..., APIClient]
) -> None:
    head = client_for(team["head"], "acme")
    assert head.put(ROUTING, {"enabled": True}, format="json").status_code == 200
    key = _create_key(head)  # the manager is not on shift
    body = {"name": "Ночной", "email": "night@example.test"}
    assert APIClient().post(_public(key["token"]), body, format="json").status_code == 202
    lead = LeadRecord.objects.using(ACME).get()
    assert lead.owner_user_id == team["head"].id and str(lead.team_id) == team["north"]


def test_director_key_leads_go_to_the_key_department(
    team: dict[str, Any], client_for: Callable[..., APIClient]
) -> None:
    boss = client_for(team["director"], "acme")
    key = _create_key(boss, department_id=team["north"])
    body = {"name": "Клиент", "phone": "+996555000111"}
    assert APIClient().post(_public(key["token"]), body, format="json").status_code == 202
    lead = LeadRecord.objects.using(ACME).get()
    assert lead.owner_user_id == team["director"].id and str(lead.team_id) == team["north"]


def test_revoked_and_unknown_tokens_are_404(
    team: dict[str, Any], client_for: Callable[..., APIClient]
) -> None:
    head = client_for(team["head"], "acme")
    key = _create_key(head)
    head.post(f"{KEYS}{key['id']}/revoke/")
    body = {"name": "Кто-то", "phone": "+996555000222"}
    public = APIClient()
    for token in (key["token"], "A" * 32, "short", "bad token!" * 4):
        response = public.post(_public(token), body, format="json")
        assert response.status_code == 404, token
        assert response.json()["error"]["message"] == "Not found."
    assert not LeadRecord.objects.using(ACME).exists()


def test_validation(team: dict[str, Any], client_for: Callable[..., APIClient]) -> None:
    key = _create_key(client_for(team["head"], "acme"))
    public = APIClient()
    url = _public(key["token"])
    assert public.post(url, {"phone": "+996555000333"}, format="json").status_code == 400
    assert public.post(url, {"name": "Без контактов"}, format="json").status_code == 400
    assert public.post(url, {"name": "Плохой", "phone": "abc"}, format="json").status_code == 400
    too_long = {"name": "Длинный", "phone": "+996555000333", "comment": "x" * 2001}
    assert public.post(url, too_long, format="json").status_code == 400
    assert public.post(url, ["not", "an", "object"], format="json").status_code == 400
    huge = {"name": "Большой", "phone": "+996555000333", "extra": "x" * 20000}
    assert public.post(url, huge, format="json").status_code == 413
    # Plain HTML form posts are accepted too.
    form = public.post(
        url,
        "name=%D0%A4%D0%BE%D1%80%D0%BC%D0%B0&email=form%40example.test",
        content_type="application/x-www-form-urlencoded",
    )
    assert form.status_code == 202, form.content
    assert LeadRecord.objects.using(ACME).count() == 1


def test_honeypot_and_duplicates_create_nothing_new(
    team: dict[str, Any], client_for: Callable[..., APIClient]
) -> None:
    key = _create_key(client_for(team["head"], "acme"))
    public = APIClient()
    url = _public(key["token"])
    bot = {"name": "Bot", "phone": "+996555000444", "website": "http://spam.test"}
    assert public.post(url, bot, format="json").status_code == 202
    assert not LeadRecord.objects.using(ACME).exists()

    person = {"name": "Человек", "phone": "+996555000555"}
    assert public.post(url, person, format="json").status_code == 202
    again = {"name": "Человек", "phone": "+996 555 000 555"}
    assert public.post(url, again, format="json").status_code == 202
    assert LeadRecord.objects.using(ACME).count() == 1
    other = {"name": "Другой", "phone": "+996555000666"}
    assert public.post(url, other, format="json").status_code == 202
    assert LeadRecord.objects.using(ACME).count() == 2


def test_throttled_by_ip_and_by_key(
    team: dict[str, Any], client_for: Callable[..., APIClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    key = _create_key(client_for(team["head"], "acme"))
    url = _public(key["token"])
    monkeypatch.setattr(WebFormIpThrottle, "rate", "2/min", raising=False)
    public = APIClient()
    for i in range(2):
        body = {"name": f"Лид {i}", "phone": f"+99655500070{i}"}
        assert public.post(url, body, format="json").status_code == 202
    blocked = public.post(url, {"name": "Ещё", "phone": "+996555000709"}, format="json")
    assert blocked.status_code == 429

    cache.clear()
    monkeypatch.setattr(WebFormIpThrottle, "rate", "100/min", raising=False)
    monkeypatch.setattr(WebFormTokenThrottle, "rate", "1/min", raising=False)
    first = APIClient(REMOTE_ADDR="10.0.0.1")
    second = APIClient(REMOTE_ADDR="10.0.0.2")
    assert (
        first.post(url, {"name": "A", "phone": "+996555000801"}, format="json").status_code == 202
    )
    assert (
        second.post(url, {"name": "B", "phone": "+996555000802"}, format="json").status_code == 429
    )


def test_cors_is_open_for_the_public_path_only(
    team: dict[str, Any], client_for: Callable[..., APIClient]
) -> None:
    key = _create_key(client_for(team["head"], "acme"))
    public = APIClient()
    origin = "https://shop.example.test"
    preflight = public.options(
        _public(key["token"]),
        HTTP_ORIGIN=origin,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type",
    )
    assert preflight["Access-Control-Allow-Origin"] == origin
    assert "Access-Control-Allow-Credentials" not in preflight
    posted = public.post(
        _public(key["token"]),
        {"name": "CORS", "phone": "+996555000900"},
        format="json",
        HTTP_ORIGIN=origin,
    )
    assert posted.status_code == 202 and posted["Access-Control-Allow-Origin"] == origin

    other = public.options(KEYS, HTTP_ORIGIN=origin, HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET")
    assert "Access-Control-Allow-Origin" not in other


def test_inactive_subscription_or_suspended_company_refuses(
    team: dict[str, Any], client_for: Callable[..., APIClient], tenants: dict[str, TenantFixture]
) -> None:
    key = _create_key(client_for(team["head"], "acme"))
    body = {"name": "Поздний", "phone": "+996555000990"}
    now = datetime.now(UTC)
    SubscriptionRecord.objects.create(
        id=uuid.uuid4(),
        organization=tenants["acme"].org,
        plan_code="pro",
        started_at=now - timedelta(days=30),
        current_period_end=now - timedelta(days=1),
    )
    refused = APIClient().post(_public(key["token"]), body, format="json")
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "subscription_inactive"

    SubscriptionRecord.objects.filter(organization=tenants["acme"].org).delete()
    tenants["acme"].org.is_active = False
    tenants["acme"].org.save(update_fields=["is_active"])
    assert APIClient().post(_public(key["token"]), body, format="json").status_code == 404
    assert not LeadRecord.objects.using(ACME).exists()
    assert WebFormKeyRecord.objects.get().leads_count == 0
