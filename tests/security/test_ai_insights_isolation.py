"""AI insights must not open a path between companies.

* a user of one company cannot request or read AI results for another
  company's call, even by guessing its id or sending another X-Tenant;
* the token sent to the AI Platform names the tenant from the CRM's active
  context only;
* AI results live in the company's own database.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import jwt
import pytest
from rest_framework.test import APIClient

from modules.ai_insights import composition
from modules.ai_insights.infrastructure.client import HttpAiPlatformClient
from modules.ai_insights.infrastructure.models import CallInsightRecord
from modules.telephony.infrastructure.models import CallRecord
from platform_core.infrastructure.tenancy.services import system_tenant_scope
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)

KEY = "security-test-ai-platform-key-0123456789ab"


@pytest.fixture
def captured(settings: Any, monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """A real HTTP client whose transport records requests instead of sending them."""
    settings.AI_PLATFORM = {**settings.AI_PLATFORM, "ENABLED": True, "URL": "http://ai.test"}
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            202, json={"schema": "ai.job.v1", "id": f"job-{len(requests)}", "status": "queued"}
        )

    client = HttpAiPlatformClient(
        base_url="http://ai.test",
        token_key=KEY,
        issuer="crm-platform",
        audience="crm-ai-platform",
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(composition, "ai_platform_client", lambda: client)
    return requests


def _call(alias: str, owner_id: Any) -> CallRecord:
    return CallRecord.objects.using(alias).create(
        id=uuid4(),
        direction="inbound",
        from_number="+77011112233",
        to_number="+77019998877",
        status="completed",
        owner_user_id=owner_id,
        notes="Globex confidential call notes.",
        created_at=datetime.now(UTC),
    )


def test_cannot_reach_another_companys_call(
    captured: list[httpx.Request],
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    alice = make_user("alice", tenant=tenants["acme"])
    bob = make_user("bob", tenant=tenants["globex"])
    globex_call = _call("tenant_globex", bob.id)
    url = f"/api/v1/ai/calls/{globex_call.id}/summary/"

    # Alice, in her own company, guessing a Globex call id: indistinguishable from "missing".
    assert client_for(alice, "acme").post(url).status_code == 404
    assert client_for(alice, "acme").get(url).status_code == 404
    # Alice naming Globex in X-Tenant: she is not a member, so the tenant does not exist for her.
    assert client_for(alice, "globex").post(url).status_code == 404

    assert captured == []
    assert not CallInsightRecord.objects.using("tenant_acme").exists()
    assert not CallInsightRecord.objects.using("tenant_globex").exists()


def test_token_names_only_the_active_tenant(
    captured: list[httpx.Request],
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    bob = make_user("bob", tenant=tenants["globex"])
    call = _call("tenant_globex", bob.id)
    insight_id = client_for(bob, "globex").post(f"/api/v1/ai/calls/{call.id}/summary/").json()["id"]

    with system_tenant_scope("globex"):
        composition.call_insight_service().submit(uuid_of(insight_id))

    assert len(captured) == 1
    token = captured[0].headers["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(token, KEY, algorithms=["HS256"], audience="crm-ai-platform")
    assert claims["tenant"] == "globex"
    assert claims["sub"] == f"user:{bob.id}"
    assert claims["scope"] == "ai.jobs.create"
    # The results stay in Globex's own database.
    assert CallInsightRecord.objects.using("tenant_globex").filter(id=insight_id).exists()
    assert not CallInsightRecord.objects.using("tenant_acme").exists()


def uuid_of(value: str) -> Any:
    from uuid import UUID

    return UUID(value)
