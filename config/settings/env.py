"""Typed and validated environment configuration.

Every environment variable the application reads is declared here. Values are
validated once at start-up; invalid configuration fails fast with a message that
names the offending variables but never echoes their values.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["local", "test", "production"]
SslMode = Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

CommaList = Annotated[list[str], NoDecode]

_PLACEHOLDER_MARKERS = ("__generate__", "change-me", "changeme", "insecure", "placeholder")
_MIN_SECRET_LENGTH = 50
_MIN_AI_TOKEN_KEY_LENGTH = 32


class ConfigurationError(RuntimeError):
    """Raised when the environment is missing or contains unsafe values."""


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


class AppEnv(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=True)

    DJANGO_ENV: Environment = "local"
    DJANGO_SECRET_KEY: SecretStr
    # Previous signing secrets still accepted during rotation (comma-separated).
    DJANGO_SECRET_KEY_FALLBACKS: CommaList = Field(default_factory=list)
    DJANGO_DEBUG: bool = False
    DJANGO_ALLOWED_HOSTS: CommaList = Field(default_factory=lambda: ["localhost", "127.0.0.1"])
    CORS_ALLOWED_ORIGINS: CommaList = Field(default_factory=list)
    CSRF_TRUSTED_ORIGINS: CommaList = Field(default_factory=list)
    SECURE_PROXY_SSL_HEADER_ENABLED: bool = False
    TRUSTED_PROXY_COUNT: int = Field(default=0, ge=0, le=5)
    API_DOCS_ENABLED: bool = False

    JWT_SIGNING_KEY: SecretStr
    # Previous JWT signing keys still accepted for verification during rotation.
    JWT_SIGNING_KEY_FALLBACKS: CommaList = Field(default_factory=list)
    JWT_ACCESS_TTL_MINUTES: int = Field(default=10, ge=1, le=60)
    JWT_REFRESH_TTL_HOURS: int = Field(default=24, ge=1, le=24 * 14)
    JWT_ISSUER: str = "crm-platform"
    JWT_AUDIENCE: str = "crm-api"

    # Email delivery (used for password-reset messages).
    EMAIL_BACKEND: str = "django.core.mail.backends.console.EmailBackend"
    EMAIL_HOST: str = "localhost"
    EMAIL_PORT: int = Field(default=587, ge=1, le=65535)
    EMAIL_HOST_USER: str = ""
    EMAIL_HOST_PASSWORD: SecretStr | None = None
    EMAIL_USE_TLS: bool = True
    DEFAULT_FROM_EMAIL: str = "no-reply@crm.local"

    # Self-service password reset.
    PASSWORD_RESET_TTL_MINUTES: int = Field(default=30, ge=5, le=1440)
    PASSWORD_RESET_URL_BASE: str = "https://app.example.com/reset-password"  # noqa: S105  # nosec B105 - a URL, not a secret

    # Account lockout after repeated failed logins.
    LOGIN_MAX_FAILURES: int = Field(default=5, ge=1, le=100)
    LOGIN_LOCKOUT_MINUTES: int = Field(default=15, ge=1, le=1440)
    LOGIN_FAILURE_WINDOW_MINUTES: int = Field(default=15, ge=1, le=1440)

    # Multi-factor authentication (TOTP). The secret at rest is encrypted; when
    # MFA_ENCRYPTION_KEY is unset a key is derived from DJANGO_SECRET_KEY.
    MFA_ISSUER: str = "CRM Platform"
    MFA_ENCRYPTION_KEY: SecretStr | None = None
    # Previous Fernet keys still accepted for decryption during rotation.
    MFA_ENCRYPTION_KEY_FALLBACKS: CommaList = Field(default_factory=list)

    # OpenID Connect relying party (log in with an external IdP). Disabled unless
    # OIDC_ENABLED and the issuer/client are configured.
    OIDC_ENABLED: bool = False
    OIDC_ISSUER: str | None = None
    OIDC_CLIENT_ID: str | None = None
    OIDC_JWKS_URI: str | None = None
    OIDC_AUTO_PROVISION: bool = False

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
    # Optional dedicated provisioning role (CREATEDB), distinct from the
    # least-privilege application role. Falls back to DB_USER when unset.
    DB_PROVISION_USER: str | None = None
    DB_PROVISION_PASSWORD: SecretStr | None = None

    # Tenant plane: one PostgreSQL database per company.
    TENANT_DB_PREFIX: str = Field(default="crm_tenant_", pattern=r"^[a-z][a-z0-9_]{1,20}$")
    TENANT_MAX_ACTIVE_ALIASES: int = Field(default=32, ge=1, le=512)
    TENANT_PROVISIONING_ENABLED: bool = False

    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = Field(default=6379, ge=1, le=65535)
    REDIS_PASSWORD: SecretStr | None = None
    REDIS_USE_TLS: bool = False
    REDIS_CA_CERT: str | None = None
    REDIS_CACHE_DB: int = Field(default=0, ge=0, le=15)
    REDIS_RESULTS_DB: int = Field(default=1, ge=0, le=15)

    RABBITMQ_HOST: str = "localhost"
    RABBITMQ_PORT: int = Field(default=5672, ge=1, le=65535)
    RABBITMQ_USER: str = "crm"
    RABBITMQ_PASSWORD: SecretStr
    RABBITMQ_VHOST: str = "crm"
    RABBITMQ_USE_TLS: bool = False
    RABBITMQ_CA_CERT: str | None = None
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

    # CRM AI Platform (separate service). Off unless explicitly enabled.
    AI_PLATFORM_ENABLED: bool = False
    AI_PLATFORM_URL: str | None = None
    # HS256 key shared with the AI Platform gateway (its AI_AUTH_HS256_KEY).
    AI_PLATFORM_TOKEN_KEY: SecretStr | None = None
    AI_PLATFORM_TOKEN_TTL_SECONDS: int = Field(default=300, ge=30, le=900)
    AI_PLATFORM_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0, le=60)
    AI_PLATFORM_DEADLINE_SECONDS: int = Field(default=900, ge=60, le=86400)
    # Language of AI texts the CRM requests (operations digests).
    AI_PLATFORM_LANGUAGE: Literal["ru", "ky", "en"] = "ru"

    @field_validator(
        "DJANGO_ALLOWED_HOSTS",
        "CORS_ALLOWED_ORIGINS",
        "CSRF_TRUSTED_ORIGINS",
        "DJANGO_SECRET_KEY_FALLBACKS",
        "JWT_SIGNING_KEY_FALLBACKS",
        "MFA_ENCRYPTION_KEY_FALLBACKS",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator(
        "DB_PROVISION_PASSWORD",
        "MFA_ENCRYPTION_KEY",
        "EMAIL_HOST_PASSWORD",
        "REDIS_PASSWORD",
        "METRICS_TOKEN",
        "SENTRY_DSN",
        "WHATSAPP_APP_SECRET",
        "INSTAGRAM_APP_SECRET",
        "TELEGRAM_WEBHOOK_SECRET",
        "TELEPHONY_WEBHOOK_SECRET",
        "AI_PLATFORM_TOKEN_KEY",
        mode="before",
    )
    @classmethod
    def _empty_as_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "AI_PLATFORM_URL",
        "OIDC_ISSUER",
        "OIDC_CLIENT_ID",
        "OIDC_JWKS_URI",
        mode="before",
    )
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

    if env.OIDC_ENABLED and not (env.OIDC_ISSUER and env.OIDC_CLIENT_ID and env.OIDC_JWKS_URI):
        errors.append("OIDC_ENABLED requires OIDC_ISSUER, OIDC_CLIENT_ID and OIDC_JWKS_URI")

    errors.extend(_ai_platform_errors(env))

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


def _ai_platform_errors(env: AppEnv) -> list[str]:
    if not env.AI_PLATFORM_ENABLED:
        return []
    errors: list[str] = []
    key = env.AI_PLATFORM_TOKEN_KEY.get_secret_value() if env.AI_PLATFORM_TOKEN_KEY else ""
    if not env.AI_PLATFORM_URL:
        errors.append("AI_PLATFORM_ENABLED requires AI_PLATFORM_URL")
    elif not env.AI_PLATFORM_URL.startswith(("http://", "https://")):
        errors.append("AI_PLATFORM_URL must be an http(s) URL")
    elif env.DJANGO_ENV == "production" and not env.AI_PLATFORM_URL.startswith("https://"):
        errors.append("AI_PLATFORM_URL must use https in production")
    # Same minimum as the AI Platform gateway enforces for this shared key.
    if len(key) < _MIN_AI_TOKEN_KEY_LENGTH:
        errors.append(
            f"AI_PLATFORM_TOKEN_KEY must be at least {_MIN_AI_TOKEN_KEY_LENGTH} characters "
            "when AI_PLATFORM_ENABLED"
        )
    elif _looks_like_placeholder(key):
        errors.append("AI_PLATFORM_TOKEN_KEY contains a placeholder value")
    if key and key in (
        env.JWT_SIGNING_KEY.get_secret_value(),
        env.DJANGO_SECRET_KEY.get_secret_value(),
    ):
        errors.append(
            "AI_PLATFORM_TOKEN_KEY must differ from JWT_SIGNING_KEY and DJANGO_SECRET_KEY"
        )
    return errors


_SECRET_FILE_SUFFIX = "_FILE"  # noqa: S105  # nosec B105 - env-var name suffix, not a secret


def secret_file_overrides(environ: Mapping[str, str]) -> dict[str, str]:
    """Resolve ``<FIELD>_FILE`` variables into values read from the named files.

    This is the first rung of secret management: a secret manager (Docker/Compose
    secrets, Kubernetes secrets, Vault Agent, SOPS) mounts the secret as a file
    and exports only its path, so the plaintext never appears in the process
    environment, ``docker inspect`` or a shell's history. For each declared field
    ``X`` we honour ``X_FILE`` unless ``X`` itself is set (an explicit value wins).
    Only declared fields are considered; unknown ``*_FILE`` variables are ignored.
    The trailing newline files commonly carry is stripped.
    """
    overrides: dict[str, str] = {}
    for key, path in environ.items():
        if not key.endswith(_SECRET_FILE_SUFFIX):
            continue
        base = key[: -len(_SECRET_FILE_SUFFIX)]
        if base not in AppEnv.model_fields or environ.get(base):
            continue
        try:
            overrides[base] = Path(path).read_text(encoding="utf-8").strip("\r\n")
        except OSError:
            raise ConfigurationError(f"{key} points to a file that cannot be read") from None
    return overrides


def load_env(env_file: Path | None = None) -> AppEnv:
    """Load and validate the environment.

    Real environment variables take precedence over the optional ``.env`` file.
    ``<FIELD>_FILE`` variables are resolved from files first (see
    :func:`secret_file_overrides`). Validation errors are re-raised without input
    values so secrets never reach logs or tracebacks.
    """
    env_files = env_file if env_file is not None and env_file.is_file() else None
    # pydantic coerces these string values per field; typed as Any for the call.
    overrides: dict[str, Any] = dict(secret_file_overrides(os.environ))
    try:
        env = AppEnv(_env_file=env_files, **overrides)  # type: ignore[call-arg]
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
