"""Settings shared by every environment. Environment-specific modules import from here."""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from celery.schedules import crontab

from config.settings.env import load_env
from platform_core.infrastructure.messaging.queues import build_task_queues

BASE_DIR = Path(__file__).resolve().parents[2]
# The .env file is a local-development convenience; tests never read it.
ENV = load_env(None if os.environ.get("DJANGO_ENV") == "test" else BASE_DIR / ".env")

SECRET_KEY = ENV.DJANGO_SECRET_KEY.get_secret_value()
DEBUG = ENV.DJANGO_DEBUG
ALLOWED_HOSTS = ENV.DJANGO_ALLOWED_HOSTS
API_DOCS_ENABLED = ENV.API_DOCS_ENABLED

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "drf_spectacular",
    "platform_core.infrastructure.messaging.apps.MessagingConfig",
    "modules.identity.infrastructure.apps.IdentityConfig",
    "modules.organizations.infrastructure.apps.OrganizationsConfig",
    "modules.tenant_registry.infrastructure.apps.TenantRegistryConfig",
    "modules.audit.infrastructure.apps.AuditConfig",
    "modules.customers.infrastructure.apps.CustomersConfig",
    "modules.leads.infrastructure.apps.LeadsConfig",
    "modules.sales_pipeline.infrastructure.apps.SalesPipelineConfig",
    "modules.activities.infrastructure.apps.ActivitiesConfig",
    "modules.sales_kpi.infrastructure.apps.SalesKpiConfig",
    "modules.reporting.infrastructure.apps.ReportingConfig",
    "modules.meetings.infrastructure.apps.MeetingsConfig",
    "modules.payments.infrastructure.apps.PaymentsConfig",
    "modules.telephony.infrastructure.apps.TelephonyConfig",
    "modules.subscriptions.infrastructure.apps.SubscriptionsConfig",
    "modules.notifications.infrastructure.apps.NotificationsConfig",
    "modules.communications.infrastructure.apps.CommunicationsConfig",
]

MIDDLEWARE = [
    "platform_core.presentation.middleware.RequestContextMiddleware",
    "platform_core.presentation.middleware.MetricsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES: list[dict[str, Any]] = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }
]

# --- Databases -------------------------------------------------------------
# "default" is the control plane. Tenant databases are registered lazily at
# runtime by platform_core.infrastructure.tenancy.connections and are never
# configured from request data.


def _database_options() -> dict[str, Any]:
    options: dict[str, Any] = {
        "connect_timeout": ENV.DB_CONNECT_TIMEOUT,
        "sslmode": ENV.DB_SSLMODE,
        "application_name": "crm-platform",
    }
    if ENV.DB_USE_PGBOUNCER:
        # Transaction pooling: no session-level startup options or server-side
        # prepared statements. statement_timeout is then set per role in PostgreSQL.
        options["prepare_threshold"] = None
    else:
        options["options"] = f"-c statement_timeout={ENV.DB_STATEMENT_TIMEOUT_MS}"
    return options


DATABASE_CLUSTERS: dict[str, dict[str, Any]] = {
    # Connection parameters per PostgreSQL cluster. Tenant registry rows refer to
    # a cluster by key; credentials stay in server-side configuration only.
    "primary": {
        "HOST": ENV.DB_HOST,
        "PORT": ENV.DB_PORT,
        "USER": ENV.DB_USER,
        "PASSWORD": ENV.DB_PASSWORD.get_secret_value(),
        # Dedicated CREATEDB role for provisioning; falls back to the app role
        # locally. In production these are two distinct PostgreSQL roles.
        "PROVISION_USER": ENV.DB_PROVISION_USER or ENV.DB_USER,
        "PROVISION_PASSWORD": (
            ENV.DB_PROVISION_PASSWORD.get_secret_value()
            if ENV.DB_PROVISION_PASSWORD is not None
            else ENV.DB_PASSWORD.get_secret_value()
        ),
        "OPTIONS": _database_options(),
        "ACCEPTS_NEW_TENANTS": True,
        "MAX_TENANTS": 500,
    }
}

DATABASES: dict[str, dict[str, Any]] = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": ENV.DB_NAME,
        "USER": ENV.DB_USER,
        "PASSWORD": ENV.DB_PASSWORD.get_secret_value(),
        "HOST": ENV.DB_HOST,
        "PORT": ENV.DB_PORT,
        "CONN_MAX_AGE": ENV.DB_CONN_MAX_AGE,
        "CONN_HEALTH_CHECKS": ENV.DB_CONN_MAX_AGE > 0,
        "DISABLE_SERVER_SIDE_CURSORS": ENV.DB_USE_PGBOUNCER,
        "OPTIONS": _database_options(),
    }
}
DATABASE_ROUTERS = ["platform_core.infrastructure.tenancy.router.TenantRouter"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

TENANCY: dict[str, Any] = {
    "ALIAS_PREFIX": "tenant_",
    "DATABASE_PREFIX": ENV.TENANT_DB_PREFIX,
    "MAX_ACTIVE_ALIASES": ENV.TENANT_MAX_ACTIVE_ALIASES,
    "PROVISIONING_ENABLED": ENV.TENANT_PROVISIONING_ENABLED,
    "HEADER": "X-Tenant",
    # Every installed app must be classified (enforced by tests).
    "CONTROL_PLANE_APPS": {
        "contenttypes",
        "auth",
        "token_blacklist",
        "identity",
        "organizations",
        "tenant_registry",
        "subscriptions",
    },
    "TENANT_PLANE_APPS": {
        "customers",
        "leads",
        "sales_pipeline",
        "activities",
        "sales_kpi",
        "reporting",
        "meetings",
        "payments",
        "telephony",
        "notifications",
        "communications",
    },
    "SHARED_APPS": {"messaging", "audit"},
    "IGNORED_APPS": {"rest_framework", "corsheaders", "drf_spectacular"},
    # Dependency inversion: platform_core only knows these ports by import path.
    "DIRECTORY": "modules.tenant_registry.infrastructure.directory.DjangoTenantDirectory",
    "MEMBERSHIPS": "modules.organizations.infrastructure.memberships.DjangoMembershipProvider",
}

# --- Cache / Redis ----------------------------------------------------------


def _redis_url(db: int) -> str:
    auth = (
        f":{quote(ENV.REDIS_PASSWORD.get_secret_value(), safe='')}@" if ENV.REDIS_PASSWORD else ""
    )
    scheme = "rediss" if ENV.REDIS_USE_TLS else "redis"
    url = f"{scheme}://{auth}{ENV.REDIS_HOST}:{ENV.REDIS_PORT}/{db}"
    if ENV.REDIS_USE_TLS:
        # Verify the server certificate when a CA is provided.
        reqs = "required" if ENV.REDIS_CA_CERT else "none"
        url += f"?ssl_cert_reqs={reqs}"
        if ENV.REDIS_CA_CERT:
            url += f"&ssl_ca_certs={quote(ENV.REDIS_CA_CERT, safe='')}"
    return url


REDIS_CACHE_URL = _redis_url(ENV.REDIS_CACHE_DB)
CACHES: dict[str, dict[str, Any]] = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_CACHE_URL,
        "KEY_PREFIX": "crm",
        "TIMEOUT": 300,
        "OPTIONS": {"socket_connect_timeout": 2, "socket_timeout": 2},
    }
}

# --- Authentication / security ------------------------------------------------

AUTH_USER_MODEL = "identity.User"
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_TRUSTED_ORIGINS = ENV.CSRF_TRUSTED_ORIGINS
DATA_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
if ENV.SECURE_PROXY_SSL_HEADER_ENABLED:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

CORS_ALLOWED_ORIGINS = ENV.CORS_ALLOWED_ORIGINS
CORS_ALLOW_CREDENTIALS = False
CORS_URLS_REGEX = r"^/api/.*$"
CORS_ALLOW_HEADERS = [
    "authorization",
    "content-type",
    "x-request-id",
    "x-correlation-id",
    "x-tenant",
]

REST_FRAMEWORK: dict[str, Any] = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        "user": "600/min",
        "auth": "10/min",
        "webhooks": "600/min",
    },
    "NUM_PROXIES": ENV.TRUSTED_PROXY_COUNT,
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": 50,
    "EXCEPTION_HANDLER": "platform_core.presentation.errors.api_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "UNAUTHENTICATED_USER": None,
}

SIMPLE_JWT: dict[str, Any] = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=ENV.JWT_ACCESS_TTL_MINUTES),
    "REFRESH_TOKEN_LIFETIME": timedelta(hours=ENV.JWT_REFRESH_TTL_HOURS),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": False,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": ENV.JWT_SIGNING_KEY.get_secret_value(),
    "AUDIENCE": ENV.JWT_AUDIENCE,
    "ISSUER": ENV.JWT_ISSUER,
    "LEEWAY": 10,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "sub",
}

# Account lockout, MFA and OIDC relying-party configuration (modules.identity).
LOGIN_LOCKOUT: dict[str, int] = {
    "MAX_FAILURES": ENV.LOGIN_MAX_FAILURES,
    "LOCKOUT_MINUTES": ENV.LOGIN_LOCKOUT_MINUTES,
    "WINDOW_MINUTES": ENV.LOGIN_FAILURE_WINDOW_MINUTES,
}
MFA: dict[str, str | None] = {
    "ISSUER": ENV.MFA_ISSUER,
    "ENCRYPTION_KEY": (
        ENV.MFA_ENCRYPTION_KEY.get_secret_value() if ENV.MFA_ENCRYPTION_KEY else None
    ),
}
OIDC: dict[str, Any] = {
    "ENABLED": ENV.OIDC_ENABLED,
    "ISSUER": ENV.OIDC_ISSUER,
    "CLIENT_ID": ENV.OIDC_CLIENT_ID,
    "JWKS_URI": ENV.OIDC_JWKS_URI,
    "AUTO_PROVISION": ENV.OIDC_AUTO_PROVISION,
}

# --- Email / password reset -------------------------------------------------
EMAIL_BACKEND = ENV.EMAIL_BACKEND
EMAIL_HOST = ENV.EMAIL_HOST
EMAIL_PORT = ENV.EMAIL_PORT
EMAIL_HOST_USER = ENV.EMAIL_HOST_USER
EMAIL_HOST_PASSWORD = ENV.EMAIL_HOST_PASSWORD.get_secret_value() if ENV.EMAIL_HOST_PASSWORD else ""
EMAIL_USE_TLS = ENV.EMAIL_USE_TLS
DEFAULT_FROM_EMAIL = ENV.DEFAULT_FROM_EMAIL
EMAIL_TIMEOUT = 10

PASSWORD_RESET: dict[str, Any] = {
    "TTL_MINUTES": ENV.PASSWORD_RESET_TTL_MINUTES,
    "URL_BASE": ENV.PASSWORD_RESET_URL_BASE,
}

SPECTACULAR_API_DESCRIPTION = """
A multi-tenant CRM backend. This page is interactive: you can read every
endpoint and call it directly with the **Try it out** button.

## Quick start (3 steps)

**1. Get an access token.** Open **Authentication -> POST /api/v1/auth/token/**,
click **Try it out**, enter your username and password, then **Execute**. Copy
the `access` value from the response.

**2. Authorize.** Click the green **Authorize** button at the top right, paste
the access token, and confirm. Every request on this page will now be sent with
`Authorization: Bearer <token>`. The token is remembered if you reload the page.

**3. Choose your company (tenant).** Business endpoints such as **Customers**
need an `X-Tenant` header with your company's short name (its "slug"), for
example `demo`. Each call has an `X-Tenant` field. Without it you get `404` on
purpose: this is how one company's data stays invisible to another.

## Good to know

- **Tokens expire.** The access token is short-lived; use
  **POST /api/v1/auth/token/refresh/** with your `refresh` token to get a new
  one, then Authorize again.
- **Permissions.** What you can see and change depends on your role in the
  company (owner, admin, manager, sales rep, viewer).
- **Errors** always look like
  `{"error": {"code": "...", "message": "...", "request_id": "..."}}`.
- **Service health** is served outside this schema: `GET /health/live`
  (process up) and `GET /health/ready` (database and cache reachable).
- **Webhooks** are called by external providers, not by you, and are verified
  by signature instead of a token.
"""

SPECTACULAR_SETTINGS: dict[str, Any] = {
    "TITLE": "CRM Platform API",
    "VERSION": "0.1.0",
    "DESCRIPTION": SPECTACULAR_API_DESCRIPTION,
    "CONTACT": {"name": "CRM Platform"},
    "LICENSE": {"name": "Proprietary"},
    "SERVE_INCLUDE_SCHEMA": False,
    "SORT_OPERATIONS": False,
    "COMPONENT_SPLIT_REQUEST": True,
    # Several endpoints have a "status" field/param with different value sets;
    # name each distinctly so the schema has stable, unambiguous enum components.
    "ENUM_NAME_OVERRIDES": {
        "LeadStatusEnum": ["new", "qualified", "unqualified", "converted"],
        "DealStatusEnum": ["open", "won", "lost"],
        "MeetingStatusEnum": ["scheduled", "completed", "cancelled"],
        "InvoiceStatusEnum": ["draft", "issued", "paid", "void"],
        "CallStatusEnum": [
            "requested",
            "ringing",
            "in_progress",
            "completed",
            "missed",
            "failed",
        ],
        "CallTerminalStatusEnum": ["completed", "missed", "failed"],
        "CallProgressStatusEnum": ["ringing", "in_progress"],
    },
    "SERVERS": [{"url": "/", "description": "This server"}],
    "TAGS": [
        {"name": "Authentication", "description": "Obtain and refresh JWT access tokens."},
        {"name": "Account", "description": "The signed-in user and the companies they belong to."},
        {
            "name": "Leads",
            "description": "Potential customers: capture, qualify, convert. Tenant-scoped.",
        },
        {
            "name": "Customers",
            "description": "Customer records. Tenant-scoped: send the X-Tenant header.",
        },
        {
            "name": "Pipelines",
            "description": "Sales funnels and their ordered stages. Tenant-scoped.",
        },
        {
            "name": "Deals",
            "description": "Opportunities moving through stages to won/lost. Tenant-scoped.",
        },
        {
            "name": "Activities",
            "description": "Tasks, calls and notes with a per-subject timeline. Tenant-scoped.",
        },
        {
            "name": "Meetings",
            "description": "Scheduling with participants and reminders. Tenant-scoped.",
        },
        {
            "name": "Payments",
            "description": "Invoices and payment status (no card data). Tenant-scoped.",
        },
        {
            "name": "Telephony",
            "description": "Call records and click-to-call. Recordings referenced by URL only.",
        },
        {
            "name": "Subscription",
            "description": "The organization's plan, limits and billing status (control plane).",
        },
        {
            "name": "Communications",
            "description": "Conversations and messages over messaging channels. Tenant-scoped.",
        },
        {
            "name": "Notifications",
            "description": "In-app notifications built from events, with per-user preferences.",
        },
        {
            "name": "Analytics",
            "description": "KPIs from the event-driven read model and async report exports.",
        },
        {
            "name": "Webhooks",
            "description": "Inbound provider callbacks. Signature-verified, not user-called.",
        },
    ],
    "SWAGGER_UI_SETTINGS": {
        "deepLinking": True,
        "persistAuthorization": True,
        "displayRequestDuration": True,
        "filter": True,
        "tryItOutEnabled": True,
        "docExpansion": "list",
        "defaultModelsExpandDepth": 1,
        "requestSnippetsEnabled": True,
        "syntaxHighlight.theme": "obsidian",
    },
    "REDOC_UI_SETTINGS": {"hideDownloadButton": False, "expandResponses": "200,201"},
}

# --- Async processing -------------------------------------------------------

_BROKER_SCHEME = "amqps" if ENV.RABBITMQ_USE_TLS else "amqp"
CELERY_BROKER_URL = (
    f"{_BROKER_SCHEME}://{quote(ENV.RABBITMQ_USER, safe='')}:"
    f"{quote(ENV.RABBITMQ_PASSWORD.get_secret_value(), safe='')}@"
    f"{ENV.RABBITMQ_HOST}:{ENV.RABBITMQ_PORT}/{quote(ENV.RABBITMQ_VHOST, safe='')}"
)
if ENV.RABBITMQ_USE_TLS:
    import ssl as _ssl

    # Encrypt the broker channel; verify the broker certificate against the CA.
    CELERY_BROKER_USE_SSL: dict[str, Any] = {
        "cert_reqs": _ssl.CERT_REQUIRED if ENV.RABBITMQ_CA_CERT else _ssl.CERT_NONE,
    }
    if ENV.RABBITMQ_CA_CERT:
        CELERY_BROKER_USE_SSL["ca_certs"] = ENV.RABBITMQ_CA_CERT
CELERY_RESULT_BACKEND = _redis_url(ENV.REDIS_RESULTS_DB)
CELERY_RESULT_EXPIRES = 3600
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
# A task that finally fails is rejected (requeue=False) so RabbitMQ dead-letters it.
CELERY_TASK_ACKS_ON_FAILURE_OR_TIMEOUT = False
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_MAX_TASKS_PER_CHILD = 1000
CELERY_WORKER_HIJACK_ROOT_LOGGER = False
CELERY_WORKER_SEND_TASK_EVENTS = True
CELERY_TASK_SEND_SENT_EVENT = True
CELERY_TASK_TIME_LIMIT = ENV.CELERY_TASK_TIME_LIMIT
CELERY_TASK_SOFT_TIME_LIMIT = ENV.CELERY_TASK_SOFT_TIME_LIMIT
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_CONNECTION_TIMEOUT = 5
CELERY_BROKER_TRANSPORT_OPTIONS = {"confirm_publish": True}
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_CREATE_MISSING_QUEUES = False
CELERY_TASK_QUEUES = build_task_queues()
CELERY_TASK_ROUTES = {
    "platform.outbox.*": {"queue": "outbox"},
    "platform.events.*": {"queue": "events"},
    "integrations.*": {"queue": "integrations"},
    "notifications.*": {"queue": "notifications"},
    "reports.*": {"queue": "reports"},
    "diagnostics.*": {"queue": "diagnostics"},
}
CELERY_BEAT_SCHEDULE = {
    "relay-outbox": {"task": "platform.outbox.relay_all", "schedule": 15.0},
    # Daily KPI snapshots for every active tenant (idempotent upsert).
    "daily-kpi-snapshots": {
        "task": "reports.compute_daily_kpis",
        "schedule": crontab(hour=1, minute=0),
    },
}
DIAGNOSTIC_TASKS_ENABLED = ENV.DIAGNOSTIC_TASKS_ENABLED

# Asynchronous report exports are written here (a shared volume in the stack).
REPORTS: dict[str, Any] = {
    "STORAGE_DIR": os.environ.get("REPORTS_STORAGE_DIR", str(BASE_DIR / "var" / "exports")),
}

# --- Integrations -----------------------------------------------------------

INTEGRATIONS: dict[str, Any] = {
    "HTTP_CONNECT_TIMEOUT": 3.0,
    "HTTP_READ_TIMEOUT": 10.0,
    "WEBHOOK_MAX_BODY_BYTES": 512 * 1024,
    "SECRETS": {
        "whatsapp": ENV.WHATSAPP_APP_SECRET,
        "instagram": ENV.INSTAGRAM_APP_SECRET,
        "telegram": ENV.TELEGRAM_WEBHOOK_SECRET,
        "telephony": ENV.TELEPHONY_WEBHOOK_SECRET,
    },
}

# --- Observability ----------------------------------------------------------

METRICS_TOKEN = ENV.METRICS_TOKEN
OBSERVABILITY: dict[str, Any] = {
    "SERVICE_NAME": ENV.OTEL_SERVICE_NAME,
    "ENVIRONMENT": ENV.DJANGO_ENV,
    "SENTRY_DSN": ENV.SENTRY_DSN,
    "SENTRY_TRACES_SAMPLE_RATE": ENV.SENTRY_TRACES_SAMPLE_RATE,
    "OTEL_ENABLED": ENV.OTEL_ENABLED,
    "OTEL_EXPORTER_OTLP_ENDPOINT": ENV.OTEL_EXPORTER_OTLP_ENDPOINT,
}

LOGGING: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "context": {"()": "platform_core.infrastructure.observability.logging.ContextFilter"},
        "redact": {"()": "platform_core.infrastructure.observability.logging.RedactionFilter"},
    },
    "formatters": {
        "json": {"()": "platform_core.infrastructure.observability.logging.JsonFormatter"},
        "console": {
            "format": "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s",
        },
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "formatter": "json" if ENV.LOG_JSON else "console",
            "filters": ["context", "redact"],
        }
    },
    "root": {"handlers": ["stdout"], "level": ENV.LOG_LEVEL},
    "loggers": {
        "django.db.backends": {"level": "WARNING"},
        "django.security": {"level": "INFO"},
        "celery": {"level": "INFO"},
    },
}

# --- Misc -------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "var" / "static"
