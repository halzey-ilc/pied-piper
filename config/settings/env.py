"""Typed and validated environment configuration.

Every environment variable the application reads is declared here. Values are
validated once at start-up; invalid configuration fails fast with a message that
names the offending variables but never echoes their values.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["local", "test", "production"]
SslMode = Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

CommaList = Annotated[list[str], NoDecode]

_PLACEHOLDER_MARKERS = ("__generate__", "change-me", "changeme", "insecure", "placeholder")
_MIN_SECRET_LENGTH = 50


class ConfigurationError(RuntimeError):
    """Raised when the environment is missing or contains unsafe values."""


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


class AppEnv(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=True)

    DJANGO_ENV: Environment = "local"
    DJANGO_SECRET_KEY: SecretStr
    DJANGO_DEBUG: bool = False
    DJANGO_ALLOWED_HOSTS: CommaList = Field(default_factory=lambda: ["localhost", "127.0.0.1"])
    CORS_ALLOWED_ORIGINS: CommaList = Field(default_factory=list)
    CSRF_TRUSTED_ORIGINS: CommaList = Field(default_factory=list)
    SECURE_PROXY_SSL_HEADER_ENABLED: bool = False
    TRUSTED_PROXY_COUNT: int = Field(default=0, ge=0, le=5)
    API_DOCS_ENABLED: bool = False

    JWT_SIGNING_KEY: SecretStr
    JWT_ACCESS_TTL_MINUTES: int = Field(default=10, ge=1, le=60)
    JWT_REFRESH_TTL_HOURS: int = Field(default=24, ge=1, le=24 * 14)
    JWT_ISSUER: str = "crm-platform"
    JWT_AUDIENCE: str = "crm-api"

    # Control-plane database (global users, organisations, tenant registry).
    DB_HOST: str = "localhost"
    DB_PORT: int = Field(default=5432, ge=1, le=65535)
    DB_NAME: str = "crm_control"
    DB_USER: str = "crm_app"
    DB_PASSWORD: SecretStr
    DB_SSLMODE: SslMode = "prefer"
    DB_CONN_MAX_AGE: int = Field(default=0, ge=0, le=600)
    DB_CONNECT_TIMEOUT: int = Field(default=5, ge=1, le=60)
    DB_STATEMENT_TIMEOUT_MS: int = Field(default=15_000, ge=100, le=600_000)
    DB_USE_PGBOUNCER: bool = False

    # Tenant plane: one PostgreSQL database per company.
    TENANT_DB_PREFIX: str = Field(default="crm_tenant_", pattern=r"^[a-z][a-z0-9_]{1,20}$")
    TENANT_MAX_ACTIVE_ALIASES: int = Field(default=32, ge=1, le=512)
    TENANT_PROVISIONING_ENABLED: bool = False

    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = Field(default=6379, ge=1, le=65535)
    REDIS_PASSWORD: SecretStr | None = None
    REDIS_CACHE_DB: int = Field(default=0, ge=0, le=15)
    REDIS_RESULTS_DB: int = Field(default=1, ge=0, le=15)

    RABBITMQ_HOST: str = "localhost"
    RABBITMQ_PORT: int = Field(default=5672, ge=1, le=65535)
    RABBITMQ_USER: str = "crm"
    RABBITMQ_PASSWORD: SecretStr
    RABBITMQ_VHOST: str = "crm"
    CELERY_TASK_TIME_LIMIT: int = Field(default=300, ge=10, le=3600)
    CELERY_TASK_SOFT_TIME_LIMIT: int = Field(default=240, ge=5, le=3600)
    DIAGNOSTIC_TASKS_ENABLED: bool = False

    LOG_LEVEL: LogLevel = "INFO"
    LOG_JSON: bool = True
    METRICS_TOKEN: SecretStr | None = None
    SENTRY_DSN: SecretStr | None = None
    SENTRY_TRACES_SAMPLE_RATE: float = Field(default=0.0, ge=0.0, le=1.0)
    OTEL_ENABLED: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT: str | None = None
    OTEL_SERVICE_NAME: str = "crm-platform"

    # Integration webhook secrets. Empty means "integration not configured".
    WHATSAPP_APP_SECRET: SecretStr | None = None
    INSTAGRAM_APP_SECRET: SecretStr | None = None
    TELEGRAM_WEBHOOK_SECRET: SecretStr | None = None
    TELEPHONY_WEBHOOK_SECRET: SecretStr | None = None

    @field_validator(
        "DJANGO_ALLOWED_HOSTS", "CORS_ALLOWED_ORIGINS", "CSRF_TRUSTED_ORIGINS", mode="before"
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator(
        "REDIS_PASSWORD",
        "METRICS_TOKEN",
        "SENTRY_DSN",
        "WHATSAPP_APP_SECRET",
        "INSTAGRAM_APP_SECRET",
        "TELEGRAM_WEBHOOK_SECRET",
        "TELEPHONY_WEBHOOK_SECRET",
        mode="before",
    )
    @classmethod
    def _empty_as_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("OTEL_EXPORTER_OTLP_ENDPOINT", mode="before")
    @classmethod
    def _empty_endpoint_as_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


def hardening_errors(env: AppEnv) -> list[str]:  # noqa: PLR0912 - one guard per hardening rule
    """Return human-readable problems with the configuration (never secret values)."""
    errors: list[str] = []
    secrets_to_check: dict[str, SecretStr | None] = {
        "DJANGO_SECRET_KEY": env.DJANGO_SECRET_KEY,
        "JWT_SIGNING_KEY": env.JWT_SIGNING_KEY,
        "DB_PASSWORD": env.DB_PASSWORD,
        "RABBITMQ_PASSWORD": env.RABBITMQ_PASSWORD,
        "REDIS_PASSWORD": env.REDIS_PASSWORD,
    }
    for name, secret in secrets_to_check.items():
        if secret is not None and _looks_like_placeholder(secret.get_secret_value()):
            errors.append(f"{name} contains a placeholder value; run scripts/init_env.sh")

    if env.DJANGO_ENV != "production":
        return errors

    if env.DJANGO_DEBUG:
        errors.append("DJANGO_DEBUG must be false in production")
    for name in ("DJANGO_SECRET_KEY", "JWT_SIGNING_KEY"):
        secret = secrets_to_check[name]
        if secret is None or len(secret.get_secret_value()) < _MIN_SECRET_LENGTH:
            errors.append(f"{name} must be at least {_MIN_SECRET_LENGTH} characters in production")
    if env.JWT_SIGNING_KEY.get_secret_value() == env.DJANGO_SECRET_KEY.get_secret_value():
        errors.append("JWT_SIGNING_KEY must differ from DJANGO_SECRET_KEY")
    if not env.DJANGO_ALLOWED_HOSTS or "*" in env.DJANGO_ALLOWED_HOSTS:
        errors.append("DJANGO_ALLOWED_HOSTS must list explicit hosts in production")
    if "*" in env.CORS_ALLOWED_ORIGINS or any(
        o.startswith("http://") for o in env.CORS_ALLOWED_ORIGINS
    ):
        errors.append("CORS_ALLOWED_ORIGINS must be explicit https origins in production")
    if env.DB_SSLMODE not in ("require", "verify-ca", "verify-full"):
        errors.append("DB_SSLMODE must be require/verify-ca/verify-full in production")
    if env.REDIS_PASSWORD is None:
        errors.append("REDIS_PASSWORD is required in production")
    if env.METRICS_TOKEN is None:
        errors.append("METRICS_TOKEN is required in production")
    if env.DIAGNOSTIC_TASKS_ENABLED:
        errors.append("DIAGNOSTIC_TASKS_ENABLED must be false in production")
    if env.API_DOCS_ENABLED:
        errors.append("API_DOCS_ENABLED must be false in production")
    return errors


def load_env(env_file: Path | None = None) -> AppEnv:
    """Load and validate the environment.

    Real environment variables take precedence over the optional ``.env`` file.
    Validation errors are re-raised without input values so secrets never reach
    logs or tracebacks.
    """
    env_files = env_file if env_file is not None and env_file.is_file() else None
    try:
        env = AppEnv(_env_file=env_files)  # type: ignore[call-arg]
    except ValidationError as exc:
        problems = [
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_input=False, include_url=False)
        ]
        raise ConfigurationError("Invalid environment: " + "; ".join(problems)) from None
    errors = hardening_errors(env)
    if errors:
        raise ConfigurationError("Unsafe environment: " + "; ".join(errors))
    return env
