"""Production refuses a partial or unsafe WhatsApp configuration."""

from __future__ import annotations

import pytest

from config.settings.env import ConfigurationError, load_env

COMPLETE = {
    "WHATSAPP_APP_SECRET": "app-secret-value-0001",
    "WHATSAPP_VERIFY_TOKEN": "v" * 64,
    "WHATSAPP_ACCESS_TOKEN": "access-token-value-0001",
    "WHATSAPP_PHONE_NUMBER_ID": "123456",
    "WHATSAPP_LEAD_OWNER_USER_ID": "11111111-1111-4111-8111-111111111111",
    "WHATSAPP_TENANT_SLUG": "demo",
}
GREEN = {
    "WHATSAPP_PROVIDER": "green_api",
    "GREEN_API_URL": "https://7103.api.green-api.example",
    "GREEN_API_INSTANCE_ID": "7103",
    "GREEN_API_TOKEN": "green-api-token-value-0001",
    "GREEN_API_WEBHOOK_TOKEN": "w" * 64,
    "WHATSAPP_LEAD_OWNER_USER_ID": COMPLETE["WHATSAPP_LEAD_OWNER_USER_ID"],
    "WHATSAPP_TENANT_SLUG": "demo",
}


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key in ("DJANGO_DEBUG", "CORS_ALLOWED_ORIGINS", "DIAGNOSTIC_TASKS_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    for key in (*COMPLETE, *GREEN):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("WHATSAPP_API_BASE_URL", raising=False)
    for key, value in {
        "DJANGO_ENV": "production",
        "DJANGO_SECRET_KEY": "s" * 30 + "0123456789abcdefghijklmnopqrstuv",
        "JWT_SIGNING_KEY": "j" * 30 + "0123456789abcdefghijklmnopqrstuv",
        "DB_PASSWORD": "db-password-value-0001",
        "RABBITMQ_PASSWORD": "rabbit-password-value-0001",
        "REDIS_PASSWORD": "redis-password-value-0001",
        "METRICS_TOKEN": "metrics-token-value-0001",
        "DJANGO_ALLOWED_HOSTS": "api.example.com",
        "DB_SSLMODE": "verify-full",
        "API_DOCS_ENABLED": "false",
    }.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


def _set(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_whatsapp_may_be_left_off_entirely(production: pytest.MonkeyPatch) -> None:
    assert load_env(None).WHATSAPP_ACCESS_TOKEN is None


def test_complete_configuration_is_accepted(production: pytest.MonkeyPatch) -> None:
    _set(production, COMPLETE)
    assert load_env(None).WHATSAPP_PHONE_NUMBER_ID == "123456"


@pytest.mark.parametrize("missing", sorted(COMPLETE))
def test_partial_configuration_names_what_is_missing(
    production: pytest.MonkeyPatch, missing: str
) -> None:
    _set(production, {k: v for k, v in COMPLETE.items() if k != missing})
    with pytest.raises(ConfigurationError, match=missing):
        load_env(None)


def test_green_api_configuration_is_accepted(production: pytest.MonkeyPatch) -> None:
    _set(production, GREEN)
    assert load_env(None).WHATSAPP_PROVIDER == "green_api"


@pytest.mark.parametrize(
    "missing",
    sorted(set(GREEN) - {"WHATSAPP_PROVIDER"}),
)
def test_partial_green_api_configuration_names_what_is_missing(
    production: pytest.MonkeyPatch, missing: str
) -> None:
    _set(production, {k: v for k, v in GREEN.items() if k != missing})
    with pytest.raises(ConfigurationError, match=missing):
        load_env(None)


def test_green_api_over_plain_http_is_refused(production: pytest.MonkeyPatch) -> None:
    _set(production, {**GREEN, "GREEN_API_URL": "http://7103.api.green-api.example"})
    with pytest.raises(ConfigurationError, match="GREEN_API_URL must use https"):
        load_env(None)


def test_short_verify_token_is_refused(production: pytest.MonkeyPatch) -> None:
    _set(production, {**COMPLETE, "WHATSAPP_VERIFY_TOKEN": "short-token"})
    with pytest.raises(ConfigurationError, match="WHATSAPP_VERIFY_TOKEN must be at least"):
        load_env(None)


def test_plain_http_api_is_refused(production: pytest.MonkeyPatch) -> None:
    _set(production, {**COMPLETE, "WHATSAPP_API_BASE_URL": "http://graph.example/v23.0"})
    with pytest.raises(ConfigurationError, match="WHATSAPP_API_BASE_URL must use https"):
        load_env(None)


def test_errors_never_contain_secret_values(production: pytest.MonkeyPatch) -> None:
    _set(production, {**COMPLETE, "WHATSAPP_VERIFY_TOKEN": "short-token"})
    with pytest.raises(ConfigurationError) as caught:
        load_env(None)
    assert "short-token" not in str(caught.value)
    assert COMPLETE["WHATSAPP_ACCESS_TOKEN"] not in str(caught.value)
