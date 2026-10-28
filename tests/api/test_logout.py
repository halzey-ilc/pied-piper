"""Logout: revoking refresh tokens via the SimpleJWT blacklist."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from tests.conftest import PASSWORD

pytestmark = pytest.mark.django_db

TOKEN_URL = "/api/v1/auth/token/"
REFRESH_URL = "/api/v1/auth/token/refresh/"


@pytest.fixture(autouse=True)
def _no_throttle(settings: Any) -> None:
    cache.clear()
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
            "auth": "10000/min",
        },
    }


def _login(username: str) -> dict[str, str]:
    response = APIClient().post(TOKEN_URL, {"username": username, "password": PASSWORD}, "json")
    assert response.status_code == 200
    return {k: str(v) for k, v in response.json().items()}


def _authed(access: str) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return client


def test_logout_revokes_the_refresh_token(make_user: Callable[..., Any]) -> None:
    make_user("sam")
    tokens = _login("sam")
    out = _authed(tokens["access"]).post(
        "/api/v1/auth/logout/", {"refresh": tokens["refresh"]}, "json"
    )
    assert out.status_code == 205
    # After logout the refresh is blacklisted and can no longer be exchanged.
    assert APIClient().post(REFRESH_URL, {"refresh": tokens["refresh"]}, "json").status_code == 401


def test_logout_rejects_an_invalid_token(make_user: Callable[..., Any]) -> None:
    make_user("ivy")
    tokens = _login("ivy")
    out = _authed(tokens["access"]).post(
        "/api/v1/auth/logout/", {"refresh": "not-a-real-token"}, "json"
    )
    assert out.status_code == 400


def test_logout_all_revokes_every_session(make_user: Callable[..., Any]) -> None:
    make_user("tess")
    first = _login("tess")
    second = _login("tess")

    out = _authed(second["access"]).post("/api/v1/auth/logout-all/", {}, "json")
    assert out.status_code == 200 and "Revoked" in out.json()["detail"]

    # Both refresh tokens are now rejected.
    assert APIClient().post(REFRESH_URL, {"refresh": first["refresh"]}, "json").status_code == 401
    assert APIClient().post(REFRESH_URL, {"refresh": second["refresh"]}, "json").status_code == 401
