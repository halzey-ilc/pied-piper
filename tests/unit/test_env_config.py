"""Environment validation (acceptance item 7: configuration)."""

from __future__ import annotations

import pytest

from config.settings.env import ConfigurationError, load_env

STRONG_SECRET = "s" * 30 + "0123456789abcdefghijklmnopqrstuv"
STRONG_JWT = "j" * 30 + "0123456789abcdefghijklmnopqrstuv"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key in (
        "DJANGO_ENV",
        "DJANGO_DEBUG",
        "DJANGO_ALLOWED_HOSTS",
        "CORS_ALLOWED_ORIGINS",
        "DJANGO_SECRET_KEY",
        "JWT_SIGNING_KEY",
        "DB_PASSWORD",
        "RABBITMQ_PASSWORD",
        "REDIS_PASSWORD",
        "METRICS_TOKEN",
        "DB_SSLMODE",
        "DIAGNOSTIC_TASKS_ENABLED",
        "API_DOCS_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _production(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    values = {
        "DJANGO_ENV": "production",
        "DJANGO_SECRET_KEY": STRONG_SECRET,
        "JWT_SIGNING_KEY": STRONG_JWT,
        "DB_PASSWORD": "db-password-value-0001",
        "RABBITMQ_PASSWORD": "rabbit-password-value-0001",
        "REDIS_PASSWORD": "redis-password-value-0001",
        "METRICS_TOKEN": "metrics-token-value-0001",
        "DJANGO_ALLOWED_HOSTS": "api.example.com",
        "DB_SSLMODE": "verify-full",
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_valid_production_environment_loads(clean_env: pytest.MonkeyPatch) -> None:
    _production(clean_env)
    env = load_env(None)
    assert env.DJANGO_ENV == "production"
    assert env.DJANGO_ALLOWED_HOSTS == ["api.example.com"]


def test_missing_required_variables_are_named_without_values(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DB_PASSWORD", "very-secret-db-password-xyz")
    with pytest.raises(ConfigurationError) as excinfo:
        load_env(None)
    message = str(excinfo.value)
    assert "DJANGO_SECRET_KEY" in message
    assert "JWT_SIGNING_KEY" in message
    assert "very-secret-db-password-xyz" not in message
    assert excinfo.value.__cause__ is None  # pydantic error (with inputs) is not chained


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"DJANGO_DEBUG": "true"}, "DJANGO_DEBUG"),
        ({"DJANGO_SECRET_KEY": "short"}, "DJANGO_SECRET_KEY"),
        ({"JWT_SIGNING_KEY": STRONG_SECRET}, "must differ"),
        ({"DJANGO_ALLOWED_HOSTS": "*"}, "DJANGO_ALLOWED_HOSTS"),
        ({"CORS_ALLOWED_ORIGINS": "http://insecure.example.com"}, "CORS_ALLOWED_ORIGINS"),
        ({"DB_SSLMODE": "disable"}, "DB_SSLMODE"),
        ({"METRICS_TOKEN": ""}, "METRICS_TOKEN"),
        ({"DIAGNOSTIC_TASKS_ENABLED": "true"}, "DIAGNOSTIC_TASKS_ENABLED"),
        ({"DB_PASSWORD": "__GENERATE__"}, "DB_PASSWORD"),
    ],
)
def test_production_rejects_unsafe_values(
    clean_env: pytest.MonkeyPatch, override: dict[str, str], expected: str
) -> None:
    _production(clean_env, **override)
    with pytest.raises(ConfigurationError, match=expected) as excinfo:
        load_env(None)
    assert STRONG_SECRET not in str(excinfo.value)


def test_placeholders_rejected_even_locally(clean_env: pytest.MonkeyPatch) -> None:
    for key, value in {
        "DJANGO_ENV": "local",
        "DJANGO_SECRET_KEY": "__GENERATE__",
        "JWT_SIGNING_KEY": STRONG_JWT,
        "DB_PASSWORD": "x" * 20,
        "RABBITMQ_PASSWORD": "y" * 20,
    }.items():
        clean_env.setenv(key, value)
    with pytest.raises(ConfigurationError, match="placeholder"):
        load_env(None)


def test_comma_separated_lists(clean_env: pytest.MonkeyPatch) -> None:
    _production(clean_env, CORS_ALLOWED_ORIGINS="https://a.example.com, https://b.example.com")
    env = load_env(None)
    assert env.CORS_ALLOWED_ORIGINS == ["https://a.example.com", "https://b.example.com"]
