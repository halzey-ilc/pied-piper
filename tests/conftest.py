"""Shared fixtures.

Two tenants ("acme", "globex") with their own statically configured databases
(see config/settings/test.py) make cross-tenant tests possible.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from modules.access_control.domain.rbac import Role
from modules.organizations.infrastructure.models import Membership, Organization
from modules.tenant_registry.infrastructure.models import TenantDatabase
from platform_core.domain.tenancy import TenantStatus

ALL_DATABASES = ["default", "tenant_acme", "tenant_globex"]
PASSWORD = "Correct-Horse-Battery-9"


@dataclass
class TenantFixture:
    org: Organization
    slug: str
    alias: str


@pytest.fixture
def tenants(db: None) -> dict[str, TenantFixture]:
    result = {}
    for slug in ("acme", "globex"):
        org = Organization.objects.create(slug=slug, name=slug.title())
        TenantDatabase.objects.create(
            organization=org,
            slug=slug,
            cluster="primary",
            database_name=f"crm_tenant_{slug}",
            status=TenantStatus.ACTIVE.value,
        )
        result[slug] = TenantFixture(org=org, slug=slug, alias=f"tenant_{slug}")
    return result


@pytest.fixture
def make_user(db: None) -> Callable[..., Any]:
    def _make(
        username: str, *, tenant: TenantFixture | None = None, role: Role = Role.OWNER
    ) -> Any:
        user = get_user_model().objects.create_user(
            username=username, email=f"{username}@example.test", password=PASSWORD
        )
        if tenant is not None:
            Membership.objects.create(user=user, organization=tenant.org, role=role.value)
        return user

    return _make


@pytest.fixture
def client_for() -> Callable[..., APIClient]:
    def _client(user: Any, tenant_slug: str | None) -> APIClient:
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
        if tenant_slug is not None:
            client.defaults["HTTP_X_TENANT"] = tenant_slug
        return client

    return _client
