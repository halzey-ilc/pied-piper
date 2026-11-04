"""Platform administration and the onboarding of a company, end to end through the API.

The platform administrator connects a company (database, director, subscription), the
director builds departments and heads of sales, a head of sales requests a manager, the
director approves, the manager works. Every rule is checked on direct API calls.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APIClient

from modules.organizations.infrastructure.models import Organization
from modules.platform_admin import composition
from modules.platform_admin.infrastructure.models import ErrorEvent
from modules.subscriptions.infrastructure.models import SubscriptionRecord
from modules.subscriptions.infrastructure.repository import DjangoSubscriptionRepository
from modules.tenant_registry.infrastructure.models import TenantDatabase
from platform_core.domain.tenancy import TenantStatus
from tests.conftest import ALL_DATABASES

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)


def _today() -> Any:
    return datetime.now(UTC).date()


@pytest.fixture(autouse=True)
def _fresh_throttle() -> None:
    # Sign-ins share the "auth" rate limit (10/min); each scenario starts from zero.
    cache.clear()


NEW_PASSWORD = "Steady-Orbit-Lantern-58"


class _StaticProvisioner:
    """Registers the company on one of the statically configured test databases.

    Real provisioning (CREATE DATABASE + migrate) is covered by the tenant_registry tests.
    """

    def provision(self, company_id: UUID, slug: str, requested_by: UUID) -> None:
        TenantDatabase.objects.create(
            organization_id=company_id,
            slug=slug,
            cluster="primary",
            database_name=f"crm_tenant_{slug}",
            status=TenantStatus.ACTIVE.value,
        )


@pytest.fixture
def provisioner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(composition, "tenant_provisioner", _StaticProvisioner)


@pytest.fixture
def admin(make_user: Callable[..., Any], client_for: Callable[..., APIClient]) -> APIClient:
    user = make_user("platform")
    user.is_platform_admin = True
    user.save()
    return client_for(user, None)


def _ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.content
    return response.json() if response.content else None


def _company_body(slug: str, **terms: Any) -> dict[str, Any]:
    return {
        "name": f"{slug.title()} LLC",
        "slug": slug,
        "subscription": {
            "plan_code": "pro",
            "starts_on": str(_today()),
            "ends_on": str(_today() + timedelta(days=90)),
            **terms,
        },
        "director": {"first_name": "Aida", "last_name": slug.title(), "login": f"dir.{slug}"},
    }


def _sign_in(login: str, temporary_password: str, tenant: str) -> APIClient:
    """Sign in with the issued temporary password, change it, return a working client."""
    anonymous = APIClient()
    token = _ok(
        anonymous.post(
            "/api/v1/auth/token/",
            {"username": login, "password": temporary_password},
            format="json",
        )
    )["access"]
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    _ok(
        client.post(
            "/api/v1/auth/password/change/",
            {"current_password": temporary_password, "new_password": NEW_PASSWORD},
            format="json",
        ),
        204,
    )
    client.defaults["HTTP_X_TENANT"] = tenant
    return client


def _connect(admin: APIClient, slug: str, **terms: Any) -> dict[str, Any]:
    created: dict[str, Any] = _ok(
        admin.post("/api/v1/platform/companies/", _company_body(slug, **terms), format="json"), 201
    )
    return created


def test_only_the_platform_administrator_reaches_the_registry(
    admin: APIClient,
    provisioner: None,
    make_user: Callable[..., Any],
    client_for: Callable[..., APIClient],
) -> None:
    created = _connect(admin, "acme")
    listed = _ok(admin.get("/api/v1/platform/companies/?search=acme"))
    assert listed["count"] == 1 and listed["results"][0]["slug"] == "acme"
    assert _ok(admin.get("/api/v1/platform/overview/"))["companies"]["total"] == 1

    director = _sign_in("dir.acme", created["director"]["temporary_password"], "acme")
    me = _ok(director.get("/api/v1/me/"))
    assert me["is_platform_admin"] is False
    assert [m["tenant"] for m in me["memberships"]] == ["acme"]
    # The director of a company is not a platform administrator.
    for path in ("overview/", "companies/", "catalog/", "errors/", "tickets/"):
        assert director.get(f"/api/v1/platform/{path}").status_code == 403
    assert (
        director.post(
            "/api/v1/platform/companies/", _company_body("globex"), format="json"
        ).status_code
        == 403
    )
    company_id = created["company"]["id"]
    assert (
        director.put(
            f"/api/v1/platform/companies/{company_id}/subscription/",
            {"plan_code": "enterprise", "starts_on": str(_today())},
            format="json",
        ).status_code
        == 403
    )
    assert APIClient().get("/api/v1/platform/companies/").status_code == 401


def test_company_onboarding_from_platform_to_manager(admin: APIClient, provisioner: None) -> None:
    created = _connect(admin, "acme")
    password = created["director"]["temporary_password"]
    assert created["company"]["directors"][0]["status"] == "invited"
    assert created["company"]["subscription"]["plan_code"] == "pro"
    # The password is kept only as a Django hash.
    stored = get_user_model().objects.get(username="dir.acme")
    assert stored.password != password and stored.check_password(password)

    director = _sign_in("dir.acme", password, "acme")
    north = _ok(director.post("/api/v1/departments/", {"name": "North"}, format="json"), 201)["id"]
    south = _ok(director.post("/api/v1/departments/", {"name": "South"}, format="json"), 201)["id"]
    rops = {}
    for name, dept in (("rop.north", north), ("rop.south", south)):
        rop = _ok(
            director.post(
                "/api/v1/staff/",
                {"first_name": name, "role": "head_of_sales", "login": name, "department_id": dept},
                format="json",
            ),
            201,
        )
        assert rop["member"]["department"]["id"] == dept
        rops[name] = _sign_in(name, rop["temporary_password"], "acme")
    rop_north, rop_south = rops["rop.north"], rops["rop.south"]

    # A head of sales creates a manager of their own department directly...
    direct = _ok(
        rop_north.post(
            "/api/v1/staff/",
            {"first_name": "Aibek", "role": "sales_rep", "login": "aibek", "department_id": south},
            format="json",
        ),
        201,
    )
    assert direct["member"]["department"]["id"] == north
    # ...cannot create or ask for a director, another head of sales or an admin...
    for role in ("director", "head_of_sales", "owner", "admin", "viewer"):
        assert (
            rop_north.post(
                "/api/v1/staff/", {"first_name": "X", "role": role}, format="json"
            ).status_code
            == 403
        )
    for role in ("director", "head_of_sales", "owner", "admin", "viewer"):
        asked = rop_north.post(
            "/api/v1/staff/requests/",
            {"kind": "hire", "first_name": "X", "role": role},
            format="json",
        )
        assert asked.status_code == 403, role
    # ...and asks for a manager of their own department (a department sent is ignored).
    request = _ok(
        rop_north.post(
            "/api/v1/staff/requests/",
            {
                "kind": "hire",
                "first_name": "Bakyt",
                "last_name": "Osmonov",
                "role": "sales_rep",
                "login": "bakyt",
                "department_id": south,
            },
            format="json",
        ),
        201,
    )
    assert request["status"] == "pending" and request["department"]["id"] == north
    assert not get_user_model().objects.filter(username="bakyt").exists()  # nothing yet

    # The other department's head of sales neither sees nor touches it.
    assert _ok(rop_south.get("/api/v1/staff/requests/"))["results"] == []
    assert rop_south.post(f"/api/v1/staff/requests/{request['id']}/cancel/").status_code == 404
    # The author cannot approve their own request.
    assert rop_north.post(f"/api/v1/staff/requests/{request['id']}/approve/").status_code == 403

    approved = _ok(director.post(f"/api/v1/staff/requests/{request['id']}/approve/"))
    assert approved["request"]["status"] == "approved"
    assert (
        director.post(f"/api/v1/staff/requests/{request['id']}/approve/").status_code == 409
    )  # decided once

    manager = _sign_in("bakyt", approved["temporary_password"], "acme")
    staff = {m["login"]: m for m in _ok(rop_north.get("/api/v1/staff/"))["results"]}
    assert staff["bakyt"]["department"]["id"] == north
    assert "bakyt" not in {m["login"] for m in _ok(rop_south.get("/api/v1/staff/"))["results"]}

    # The manager works with their own records and nothing more.
    customer = _ok(manager.post("/api/v1/customers/", {"name": "Client LLC"}, format="json"), 201)
    assert customer["id"] in {c["id"] for c in _ok(manager.get("/api/v1/customers/"))["results"]}
    assert manager.get("/api/v1/staff/").status_code == 403
    assert manager.get("/api/v1/team/today/").status_code == 403
    assert (
        manager.post(
            "/api/v1/staff/requests/",
            {"kind": "hire", "first_name": "Y", "role": "sales_rep"},
            format="json",
        ).status_code
        == 403
    )
    assert manager.get("/api/v1/platform/companies/").status_code == 403

    # Dismissal: requested by the head of sales, done by the director.
    dismissal = _ok(
        rop_north.post(
            "/api/v1/staff/requests/",
            {
                "kind": "dismiss",
                "membership_id": staff["bakyt"]["id"],
                "reason": "Probation",
            },
            format="json",
        ),
        201,
    )
    assert manager.get("/api/v1/customers/").status_code == 200  # still works until approved
    _ok(director.post(f"/api/v1/staff/requests/{dismissal['id']}/approve/"))
    assert manager.get("/api/v1/customers/").status_code == 401  # the account is disabled


def test_another_company_sees_nothing_of_the_new_one(admin: APIClient, provisioner: None) -> None:
    acme = _connect(admin, "acme")
    globex = _connect(admin, "globex")
    acme_director = _sign_in("dir.acme", acme["director"]["temporary_password"], "acme")
    globex_director = _sign_in("dir.globex", globex["director"]["temporary_password"], "globex")
    _ok(acme_director.post("/api/v1/customers/", {"name": "Secret client"}, format="json"), 201)
    dept = _ok(acme_director.post("/api/v1/departments/", {"name": "North"}, format="json"), 201)

    assert all(
        c["name"] != "Secret client"
        for c in _ok(globex_director.get("/api/v1/customers/"))["results"]
    )
    as_acme = APIClient()
    as_acme.credentials(**globex_director._credentials)  # type: ignore[attr-defined]
    as_acme.defaults["HTTP_X_TENANT"] = "acme"
    assert as_acme.get("/api/v1/customers/").status_code == 404
    assert as_acme.get("/api/v1/staff/requests/").status_code == 404
    # A director cannot create staff in another company's department.
    hired = globex_director.post(
        "/api/v1/staff/",
        {"first_name": "X", "role": "sales_rep", "department_id": dept["id"]},
        format="json",
    )
    assert hired.status_code == 400


def test_subscription_terms_and_modules_are_enforced_and_persisted(
    admin: APIClient, provisioner: None
) -> None:
    created = _connect(admin, "acme", plan_code="free", features=["telephony"])
    company_id = created["company"]["id"]
    director = _sign_in("dir.acme", created["director"]["temporary_password"], "acme")

    # Modules: switched-on telephony works, payments (not switched on) is refused.
    assert director.get("/api/v1/telephony/calls/").status_code == 200
    refused = director.get("/api/v1/payments/")
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "feature_not_in_plan"

    # Saved in the database: a fresh repository (as after a restart) reads the same terms.
    stored = DjangoSubscriptionRepository().get_by_org(UUID(company_id))
    assert stored is not None
    assert stored.plan_code.value == "free" and {f.value for f in stored.features or ()} == {
        "telephony"
    }
    _ok(
        admin.put(
            f"/api/v1/platform/companies/{company_id}/subscription/",
            {
                "plan_code": "pro",
                "starts_on": str(_today() - timedelta(days=30)),
                "ends_on": str(_today() - timedelta(days=1)),
                "features": ["telephony", "payments"],
            },
            format="json",
        )
    )
    row = SubscriptionRecord.objects.get(organization_id=company_id)
    assert row.plan_code == "pro" and sorted(row.features or []) == ["payments", "telephony"]

    # Expired: business data closed, the subscription state and support stay reachable.
    closed = director.get("/api/v1/customers/")
    assert closed.status_code == 403
    assert closed.json()["error"]["code"] == "subscription_inactive"
    assert director.get("/api/v1/subscription/").status_code == 200
    assert director.get("/api/v1/me/").status_code == 200
    card = _ok(admin.get(f"/api/v1/platform/companies/{company_id}/"))
    assert card["subscription"]["access_state"] == "expired"
    expired = _ok(admin.get("/api/v1/platform/companies/?access=expired"))
    assert [c["id"] for c in expired["results"]] == [company_id]

    # Extended: open again, and the new module works.
    _ok(
        admin.put(
            f"/api/v1/platform/companies/{company_id}/subscription/",
            {
                "plan_code": "pro",
                "starts_on": str(_today()),
                "ends_on": str(_today() + timedelta(days=5)),
                "features": ["telephony", "payments"],
            },
            format="json",
        )
    )
    assert director.get("/api/v1/customers/").status_code == 200
    assert director.get("/api/v1/payments/").status_code == 200
    card = _ok(admin.get(f"/api/v1/platform/companies/{company_id}/"))
    assert card["subscription"]["access_state"] == "expiring"
    assert {h["action"] for h in card["history"]} >= {
        "platform.company_created",
        "platform.subscription_assigned",
    }

    # Suspended by the platform: the company is closed to its members.
    _ok(
        admin.patch(
            f"/api/v1/platform/companies/{company_id}/", {"is_active": False}, format="json"
        )
    )
    assert director.get("/api/v1/customers/").status_code == 404


def test_a_failed_connection_leaves_no_partial_company(admin: APIClient) -> None:
    # Provisioning is disabled in the test settings: the database step fails last.
    response = admin.post("/api/v1/platform/companies/", _company_body("acme"), format="json")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "provisioning_disabled"
    assert not Organization.objects.filter(slug="acme").exists()
    assert not get_user_model().objects.filter(username="dir.acme").exists()
    assert not SubscriptionRecord.objects.exists()


def test_unexpected_errors_are_recorded_without_details(
    admin: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("secret token abc123 leaked")

    original = composition.platform_admin_service
    monkeypatch.setattr(
        "modules.platform_admin.presentation.views.platform_admin_service",
        type("Broken", (), {"overview": broken}),
    )
    failed = admin.get("/api/v1/platform/overview/")
    assert failed.status_code == 500
    monkeypatch.setattr(
        "modules.platform_admin.presentation.views.platform_admin_service", original
    )

    event = ErrorEvent.objects.get()
    assert event.exception_type == "RuntimeError"
    assert event.route == "api/v1/platform/overview/"
    assert event.request_id == failed.json()["error"]["request_id"]
    assert "secret" not in str(vars(event))

    errors = _ok(admin.get("/api/v1/platform/errors/?status=new"))
    assert errors["count"] == 1
    resolved = _ok(
        admin.patch(f"/api/v1/platform/errors/{event.id}/", {"status": "resolved"}, format="json")
    )
    assert resolved["status"] == "resolved"


def test_support_requests_from_a_company_to_the_platform(
    admin: APIClient, provisioner: None
) -> None:
    created = _connect(admin, "acme")
    director = _sign_in("dir.acme", created["director"]["temporary_password"], "acme")
    sent = _ok(
        director.post(
            "/api/v1/support/tickets/",
            {"kind": "problem", "subject": "Report does not open", "body": "An error on opening"},
            format="json",
        ),
        201,
    )
    assert sent["status"] == "new" and "internal_note" not in sent

    listed = _ok(admin.get("/api/v1/platform/tickets/?status=new"))
    assert [t["id"] for t in listed["results"]] == [sent["id"]]
    assert listed["results"][0]["company"]["name"] == created["company"]["name"]
    updated = _ok(
        admin.patch(
            f"/api/v1/platform/tickets/{sent['id']}/",
            {"status": "in_progress", "internal_note": "Check report permissions"},
            format="json",
        )
    )
    assert updated["internal_note"] == "Check report permissions"

    mine = _ok(director.get("/api/v1/support/tickets/"))["results"]
    assert mine[0]["status"] == "in_progress" and "internal_note" not in mine[0]


def test_a_connected_company_starts_with_the_standard_pipeline(
    tenants: dict[str, Any],
) -> None:
    from modules.sales_pipeline.composition import ensure_starter_pipeline
    from modules.sales_pipeline.infrastructure.repository import DjangoPipelineRepository
    from platform_core.infrastructure.tenancy.services import system_tenant_scope

    with system_tenant_scope("acme") as tenant:
        assert ensure_starter_pipeline(tenant.db_alias) is True
        assert ensure_starter_pipeline(tenant.db_alias) is False  # a retry adds nothing
        (pipeline,) = DjangoPipelineRepository(tenant.db_alias).list_all()
    assert pipeline.is_default
    # The system stages: «Встреча перенесена» after the open ones, «Продажа» always last.
    assert [(s.name, s.kind.value) for s in pipeline.stages][-3:] == [
        ("Встреча перенесена", "open"),
        ("Проиграна", "lost"),
        ("Продажа", "won"),
    ]


def test_platform_admin_issues_new_director_credentials(
    admin: APIClient,
    provisioner: None,
    make_user: Callable[..., Any],
    client_for: Callable[..., APIClient],
    caplog: pytest.LogCaptureFixture,
) -> None:
    created = _connect(admin, "acme")
    company_id = created["company"]["id"]
    first_password = created["director"]["temporary_password"]
    director = _sign_in("dir.acme", first_password, "acme")
    director_id = _ok(director.get("/api/v1/me/"))["id"]
    refresh = (
        APIClient()
        .post(
            "/api/v1/auth/token/", {"username": "dir.acme", "password": NEW_PASSWORD}, format="json"
        )
        .json()["refresh"]
    )

    url = f"/api/v1/platform/companies/{company_id}/directors/{director_id}/credentials/"
    issued = _ok(admin.post(url))
    assert issued["login"] == "dir.acme"
    new_password = issued["temporary_password"]
    assert new_password not in (first_password, NEW_PASSWORD)
    user = get_user_model().objects.get(username="dir.acme")
    assert user.must_change_password is True and user.check_password(new_password)
    # Old passwords and the signed-in session no longer work; the secret is not logged.
    for old in (first_password, NEW_PASSWORD):
        denied = APIClient().post(
            "/api/v1/auth/token/", {"username": "dir.acme", "password": old}, format="json"
        )
        assert denied.status_code == 401
    stale = APIClient().post("/api/v1/auth/token/refresh/", {"refresh": refresh}, format="json")
    assert stale.status_code == 401
    assert new_password not in caplog.text
    # The new temporary password works and must be changed first.
    _sign_in("dir.acme", new_password, "acme")
    card = _ok(admin.get(f"/api/v1/platform/companies/{company_id}/"))
    assert "platform.director_credentials_issued" in {h["action"] for h in card["history"]}
    assert new_password not in str(card)

    # Only an active director of THIS company, only by the platform administrator.
    other = make_user("someone", tenant=None)
    assert (
        admin.post(
            f"/api/v1/platform/companies/{company_id}/directors/{other.id}/credentials/"
        ).status_code
        == 404
    )
    # A director is never a platform administrator.
    assert client_for(user, None).post(url).status_code == 403


def test_company_staff_counts_follow_the_current_structure(
    admin: APIClient, provisioner: None
) -> None:
    created = _connect(admin, "acme")
    director = _sign_in("dir.acme", created["director"]["temporary_password"], "acme")
    north = _ok(director.post("/api/v1/departments/", {"name": "North"}, format="json"), 201)["id"]
    _ok(director.post("/api/v1/departments/", {"name": "South"}, format="json"), 201)

    def hire(login: str, role: str) -> Any:
        body = {"first_name": login, "role": role, "login": login, "department_id": north}
        return _ok(director.post("/api/v1/staff/", body, format="json"), 201)

    hire("rop.north", "head_of_sales")
    hire("m.one", "sales_rep")
    leaving = hire("m.two", "sales_rep")
    deactivated = director.post(f"/api/v1/staff/{leaving['member']['id']}/deactivate/")
    assert deactivated.status_code == 200, deactivated.content

    company_id = created["company"]["id"]
    expected = {"employees": 3, "leaders": 1, "departments": 2, "heads_of_sales": 1, "managers": 1}
    listed = _ok(admin.get("/api/v1/platform/companies/?search=acme"))["results"]
    assert [row["staff"] for row in listed] == [expected]
    assert _ok(admin.get(f"/api/v1/platform/companies/{company_id}/"))["staff"] == expected


def test_trial_lasts_seven_days_and_the_overview_narrows_to_a_plan(
    admin: APIClient, provisioner: None
) -> None:
    trial = _connect(admin, "acme", plan_code="trial", ends_on=None)
    _connect(admin, "globex")
    # A trial without an end date ends on the seventh day, inclusive.
    assert trial["company"]["subscription"]["ends_on"] == str(_today() + timedelta(days=6))

    everything = _ok(admin.get("/api/v1/platform/overview/"))
    assert everything["plan"] is None and everything["companies"]["total"] == 2
    by_plan = {row["plan"]: row for row in everything["revenue"]["by_plan"]}
    assert list(by_plan) == ["trial", "free", "pro", "enterprise"]
    assert by_plan["pro"]["companies"] == 1 and by_plan["pro"]["amount"] == "49"
    assert by_plan["trial"]["companies"] == 1 and by_plan["trial"]["amount"] == "0"

    narrowed = _ok(admin.get("/api/v1/platform/overview/?plan=trial"))
    assert narrowed["plan"] == "trial"
    assert narrowed["companies"]["total"] == 1 and narrowed["users"]["active"] == 1
    assert narrowed["subscriptions"]["active"] == 0 and narrowed["subscriptions"]["expiring"] == 1
    assert {r["plan"]: r["companies"] for r in narrowed["revenue"]["by_plan"]}["pro"] == 0

    assert admin.get("/api/v1/platform/overview/?plan=gold").status_code == 400
