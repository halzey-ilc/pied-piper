"""Self-service password reset: request (no enumeration) and single-use confirm."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from modules.identity.infrastructure.models import PasswordResetToken
from tests.conftest import PASSWORD

pytestmark = pytest.mark.django_db

NEW_PASSWORD = "New-Str0ng-Passphrase-42"
REQUEST_URL = "/api/v1/auth/password-reset/"
CONFIRM_URL = "/api/v1/auth/password-reset/confirm/"


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


def _token_from(mailoutbox: list[Any]) -> str:
    match = re.search(r"token=(\S+)", mailoutbox[-1].body)
    assert match is not None
    return match.group(1)


def test_request_sends_email_for_known_account(
    make_user: Callable[..., Any], mailoutbox: list[Any]
) -> None:
    make_user("alice")
    response = APIClient().post(REQUEST_URL, {"email": "alice@example.test"}, format="json")
    assert response.status_code == 200
    assert len(mailoutbox) == 1 and "alice@example.test" in mailoutbox[0].to
    assert PasswordResetToken.objects.count() == 1


def test_request_is_silent_for_unknown_account(mailoutbox: list[Any]) -> None:
    response = APIClient().post(REQUEST_URL, {"email": "nobody@example.test"}, format="json")
    assert response.status_code == 200  # same response, no enumeration
    assert len(mailoutbox) == 0
    assert PasswordResetToken.objects.count() == 0


def test_confirm_sets_new_password_and_consumes_token(
    make_user: Callable[..., Any], mailoutbox: list[Any]
) -> None:
    make_user("bob")
    client = APIClient()
    client.post(REQUEST_URL, {"email": "bob@example.test"}, format="json")
    token = _token_from(mailoutbox)

    confirmed = client.post(
        CONFIRM_URL, {"token": token, "new_password": NEW_PASSWORD}, format="json"
    )
    assert confirmed.status_code == 200
    # The new password works; the old one does not.
    assert (
        client.post(
            "/api/v1/auth/token/", {"username": "bob", "password": NEW_PASSWORD}, format="json"
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/auth/token/", {"username": "bob", "password": PASSWORD}, format="json"
        ).status_code
        == 401
    )
    # Token cannot be reused.
    assert (
        client.post(
            CONFIRM_URL, {"token": token, "new_password": "Another-Str0ng-1!"}, format="json"
        ).status_code
        == 400
    )


def test_confirm_rejects_expired_token(
    make_user: Callable[..., Any], mailoutbox: list[Any]
) -> None:
    make_user("carol")
    client = APIClient()
    client.post(REQUEST_URL, {"email": "carol@example.test"}, format="json")
    token = _token_from(mailoutbox)
    PasswordResetToken.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
    assert (
        client.post(
            CONFIRM_URL, {"token": token, "new_password": NEW_PASSWORD}, format="json"
        ).status_code
        == 400
    )


def test_confirm_enforces_password_policy(
    make_user: Callable[..., Any], mailoutbox: list[Any]
) -> None:
    make_user("dave")
    client = APIClient()
    client.post(REQUEST_URL, {"email": "dave@example.test"}, format="json")
    token = _token_from(mailoutbox)
    weak = client.post(CONFIRM_URL, {"token": token, "new_password": "short"}, format="json")
    assert weak.status_code == 400 and weak.json()["error"]["code"] == "validation_error"


def test_reset_unlocks_a_locked_account(
    make_user: Callable[..., Any], mailoutbox: list[Any], settings: Any
) -> None:
    settings.LOGIN_LOCKOUT = {"MAX_FAILURES": 2, "LOCKOUT_MINUTES": 15, "WINDOW_MINUTES": 15}
    make_user("erin")
    client = APIClient()
    for _ in range(2):
        client.post("/api/v1/auth/token/", {"username": "erin", "password": "x"}, format="json")
    locked = client.post(
        "/api/v1/auth/token/", {"username": "erin", "password": PASSWORD}, format="json"
    )
    assert locked.status_code == 429

    client.post(REQUEST_URL, {"email": "erin@example.test"}, format="json")
    token = _token_from(mailoutbox)
    client.post(CONFIRM_URL, {"token": token, "new_password": NEW_PASSWORD}, format="json")
    # Reset cleared the lockout: the account can log in again immediately.
    assert (
        client.post(
            "/api/v1/auth/token/", {"username": "erin", "password": NEW_PASSWORD}, format="json"
        ).status_code
        == 200
    )
