"""Account lockout, MFA (TOTP + recovery) and OIDC login at the API boundary."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.cache import cache
from rest_framework.test import APIClient

from modules.identity.domain import totp
from tests.conftest import PASSWORD

pytestmark = pytest.mark.django_db

OIDC_ISSUER = "https://idp.example.com"
OIDC_CLIENT_ID = "crm-client"


@pytest.fixture(autouse=True)
def _no_throttle(settings: Any) -> None:
    """Keep rate limiting out of the way; these tests exercise lockout/MFA logic."""
    cache.clear()
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
            "auth": "10000/min",
        },
    }


def _login(client: APIClient, username: str, password: str, otp: str | None = None) -> Any:
    body: dict[str, str] = {"username": username, "password": password}
    if otp is not None:
        body["otp"] = otp
    return client.post("/api/v1/auth/token/", body, format="json")


def test_account_locks_after_repeated_failures(
    make_user: Callable[..., Any], settings: Any
) -> None:
    settings.LOGIN_LOCKOUT = {"MAX_FAILURES": 3, "LOCKOUT_MINUTES": 15, "WINDOW_MINUTES": 15}
    make_user("locke")
    client = APIClient()
    for _ in range(3):
        assert _login(client, "locke", "wrong-password").status_code == 401
    # Even the correct password is refused while locked.
    locked = _login(client, "locke", PASSWORD)
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "account_locked"


def test_successful_login_clears_failures(make_user: Callable[..., Any], settings: Any) -> None:
    settings.LOGIN_LOCKOUT = {"MAX_FAILURES": 3, "LOCKOUT_MINUTES": 15, "WINDOW_MINUTES": 15}
    make_user("clearme")
    client = APIClient()
    assert _login(client, "clearme", "nope").status_code == 401
    assert _login(client, "clearme", "nope").status_code == 401
    assert _login(client, "clearme", PASSWORD).status_code == 200  # resets the counter
    for _ in range(2):
        assert _login(client, "clearme", "nope").status_code == 401  # would have locked at 3rd
    assert _login(client, "clearme", PASSWORD).status_code == 200


def test_mfa_enrol_confirm_and_enforced_login(
    make_user: Callable[..., Any], client_for: Callable[..., APIClient]
) -> None:
    user = make_user("mfauser")
    client = client_for(user, None)

    secret = client.post("/api/v1/auth/mfa/enroll/", {}, format="json").json()["secret"]
    confirm = client.post(
        "/api/v1/auth/mfa/confirm/", {"code": totp.generate(secret)}, format="json"
    )
    assert confirm.status_code == 200
    recovery = confirm.json()["recovery_codes"]
    assert len(recovery) == 10

    anon = APIClient()
    # Correct password alone is no longer enough.
    challenge = _login(anon, "mfauser", PASSWORD)
    assert challenge.status_code == 401 and challenge.json()["error"]["code"] == "mfa_required"
    # With a current code it succeeds.
    ok = _login(anon, "mfauser", PASSWORD, otp=totp.generate(secret))
    assert ok.status_code == 200 and "access" in ok.json()
    # A wrong code is refused.
    assert _login(anon, "mfauser", PASSWORD, otp="000000").status_code == 401


def test_recovery_code_is_single_use(
    make_user: Callable[..., Any], client_for: Callable[..., APIClient]
) -> None:
    user = make_user("recovery")
    client = client_for(user, None)
    secret = client.post("/api/v1/auth/mfa/enroll/", {}, format="json").json()["secret"]
    recovery = client.post(
        "/api/v1/auth/mfa/confirm/", {"code": totp.generate(secret)}, format="json"
    ).json()["recovery_codes"]

    anon = APIClient()
    assert _login(anon, "recovery", PASSWORD, otp=recovery[0]).status_code == 200
    # The same recovery code cannot be used twice.
    assert _login(anon, "recovery", PASSWORD, otp=recovery[0]).status_code == 401


def test_mfa_disable_requires_password(
    make_user: Callable[..., Any], client_for: Callable[..., APIClient]
) -> None:
    user = make_user("disabler")
    client = client_for(user, None)
    secret = client.post("/api/v1/auth/mfa/enroll/", {}, format="json").json()["secret"]
    client.post("/api/v1/auth/mfa/confirm/", {"code": totp.generate(secret)}, format="json")

    assert (
        client.post("/api/v1/auth/mfa/disable/", {"password": "wrong"}, format="json").status_code
        == 400
    )
    assert (
        client.post("/api/v1/auth/mfa/disable/", {"password": PASSWORD}, format="json").status_code
        == 204
    )
    # MFA no longer required after disabling.
    assert _login(APIClient(), "disabler", PASSWORD).status_code == 200


# --- OIDC login -------------------------------------------------------------


class _StaticResolver:
    def __init__(self, public_key: Any) -> None:
        self._public_key = public_key

    def get_key(self, kid: str | None) -> Any:
        return self._public_key


def _oidc_setup(settings: Any, monkeypatch: pytest.MonkeyPatch, *, auto_provision: bool) -> Any:
    from modules.identity.infrastructure import oidc
    from modules.identity.presentation import views

    settings.OIDC = {
        "ENABLED": True,
        "ISSUER": OIDC_ISSUER,
        "CLIENT_ID": OIDC_CLIENT_ID,
        "JWKS_URI": "https://idp.example.com/jwks",
        "AUTO_PROVISION": auto_provision,
    }
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = oidc.OidcVerifier(
        issuer=OIDC_ISSUER,
        client_id=OIDC_CLIENT_ID,
        resolver=_StaticResolver(private_key.public_key()),
    )
    monkeypatch.setattr(views, "verifier_from_settings", lambda: verifier)
    return private_key


def _id_token(private_key: Any, email: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": OIDC_ISSUER,
            "aud": OIDC_CLIENT_ID,
            "sub": "idp-123",
            "email": email,
            "email_verified": True,
            "iat": now,
            "exp": now + 300,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "k1"},
    )


def test_oidc_login_known_user_issues_tokens(
    make_user: Callable[..., Any], settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_key = _oidc_setup(settings, monkeypatch, auto_provision=False)
    make_user("oidcuser")  # email is oidcuser@example.test
    token = _id_token(private_key, "oidcuser@example.test")
    response = APIClient().post("/api/v1/auth/oidc/", {"id_token": token}, format="json")
    assert response.status_code == 200
    assert "access" in response.json() and "refresh" in response.json()


def test_oidc_unknown_user_rejected_without_auto_provision(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_key = _oidc_setup(settings, monkeypatch, auto_provision=False)
    token = _id_token(private_key, "stranger@example.test")
    response = APIClient().post("/api/v1/auth/oidc/", {"id_token": token}, format="json")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "oidc_no_account"


def test_oidc_auto_provisions_when_enabled(settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from django.contrib.auth import get_user_model

    private_key = _oidc_setup(settings, monkeypatch, auto_provision=True)
    token = _id_token(private_key, "newcomer@example.test")
    response = APIClient().post("/api/v1/auth/oidc/", {"id_token": token}, format="json")
    assert response.status_code == 200
    assert get_user_model().objects.filter(email="newcomer@example.test").exists()
