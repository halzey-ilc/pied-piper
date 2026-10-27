"""Test settings.

Safe, non-secret defaults are applied only when the variables are not already
set, so the suite can run on a developer host (unit tests) and inside the
docker compose ``tests`` service (integration tests against real services).
"""

import os
from typing import Any

_TEST_DEFAULTS = {
    "DJANGO_ENV": "test",
    # Non-secret, test-only values applied via setdefault (real env still wins). # nosec B105
    "DJANGO_SECRET_KEY": "test-only-secret-key-not-used-anywhere-else-0123456789abcdef",  # nosec B105
    "JWT_SIGNING_KEY": "test-only-jwt-signing-key-not-used-anywhere-else-0123456789",  # nosec B105
    "DB_PASSWORD": "test-only-db-password",  # nosec B105
    "RABBITMQ_PASSWORD": "test-only-rabbitmq-password",  # nosec B105
    "LOG_JSON": "false",
    "LOG_LEVEL": "WARNING",
    "DIAGNOSTIC_TASKS_ENABLED": "true",
    "TENANT_PROVISIONING_ENABLED": "false",
    "TENANT_MAX_ACTIVE_ALIASES": "4",
}
for _key, _value in _TEST_DEFAULTS.items():
    os.environ.setdefault(_key, _value)
# Tests must never pick up a developer's .env file implicitly.
os.environ["DJANGO_ENV"] = "test"

from django.core.exceptions import ImproperlyConfigured  # noqa: E402

from config.settings.base import *  # noqa: E402
from config.settings.base import DATABASES, ENV, TENANCY  # noqa: E402

if ENV.DJANGO_ENV != "test":
    raise ImproperlyConfigured("config.settings.test requires DJANGO_ENV=test")

# Two statically configured tenant databases make cross-tenant tests possible.
TEST_TENANT_SLUGS = ("acme", "globex")
for _slug in TEST_TENANT_SLUGS:
    _alias = f"{TENANCY['ALIAS_PREFIX']}{_slug}"
    _db: dict[str, Any] = {**DATABASES["default"], "NAME": f"{TENANCY['DATABASE_PREFIX']}{_slug}"}
    _db["TEST"] = {"NAME": f"test_{TENANCY['DATABASE_PREFIX']}{_slug}"}
    DATABASES[_alias] = _db

CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "tests"}
}
API_DOCS_ENABLED = True
