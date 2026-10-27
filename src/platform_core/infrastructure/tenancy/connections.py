"""Bounded, lazily populated registry of tenant database aliases.

* Aliases are registered only when a tenant is actually used.
* At most ``max_aliases`` tenant aliases exist per process; the least recently
  used alias is closed and removed when the limit is exceeded.
* Connection parameters come from server-side cluster configuration; the tenant
  registry only supplies the cluster key and database name.

Connections themselves are opened lazily by Django and, with CONN_MAX_AGE=0,
closed at the end of each request/task. Pooling belongs to PgBouncer.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any

from django.conf import settings
from django.db import connections

from platform_core.domain.tenancy import TenantDatabaseLocator, validate_database_name

logger = logging.getLogger(__name__)


class TenantRoutingError(RuntimeError):
    pass


def alias_for_slug(slug: str, prefix: str) -> str:
    return prefix + slug.replace("-", "_")


class DjangoTenantConnectionRegistry:
    def __init__(
        self,
        clusters: Mapping[str, Mapping[str, Any]],
        *,
        alias_prefix: str,
        max_aliases: int,
    ) -> None:
        if max_aliases < 1:
            raise ValueError("max_aliases must be >= 1")
        self._clusters = clusters
        self._prefix = alias_prefix
        self._max = max_aliases
        self._lru: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()
        # Aliases configured statically in settings. Captured now, because
        # registering a dynamic alias writes it into the same settings.DATABASES
        # dict and would otherwise be mistaken for a static one.
        self._static: frozenset[str] = frozenset(settings.DATABASES)

    @property
    def active_aliases(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._lru)

    def ensure_alias(self, locator: TenantDatabaseLocator) -> str:
        alias = alias_for_slug(locator.slug, self._prefix)
        if alias in self._static:
            # Statically configured (tests / single-tenant local setups).
            return alias
        with self._lock:
            if alias in self._lru:
                self._lru.move_to_end(alias)
                return alias
            connections.settings[alias] = self._build_settings(alias, locator)
            self._lru[alias] = None
            evicted = []
            while len(self._lru) > self._max:
                old_alias, _ = self._lru.popitem(last=False)
                evicted.append(old_alias)
        for old_alias in evicted:
            self._drop(old_alias)
        return alias

    def release(self, alias: str) -> None:
        """Close this thread's connection for a dynamic ``alias`` (keeps it registered).

        Statically configured aliases follow Django's normal connection lifecycle,
        and a connection inside an open transaction is never closed here.
        """
        if alias in self._static:
            return
        for conn in connections.all(initialized_only=True):
            if conn.alias == alias and not conn.in_atomic_block:
                conn.close()

    def _drop(self, alias: str) -> None:
        self.release(alias)
        with contextlib.suppress(AttributeError):
            del connections[alias]  # AttributeError if never initialised in this thread
        connections.settings.pop(alias, None)
        logger.info("tenant_alias_evicted", extra={"db_alias": alias})

    def _build_settings(self, alias: str, locator: TenantDatabaseLocator) -> dict[str, Any]:
        cluster = self._clusters.get(locator.cluster)
        if cluster is None:
            raise TenantRoutingError(f"Unknown database cluster {locator.cluster!r}")
        raw = {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": validate_database_name(locator.database_name),
            "USER": cluster["USER"],
            "PASSWORD": cluster["PASSWORD"],
            "HOST": cluster["HOST"],
            "PORT": cluster["PORT"],
            "OPTIONS": dict(cluster.get("OPTIONS", {})),
            "CONN_MAX_AGE": 0,
            "DISABLE_SERVER_SIDE_CURSORS": settings.DATABASES["default"].get(
                "DISABLE_SERVER_SIDE_CURSORS", False
            ),
        }
        # Let Django fill in the remaining defaults exactly as for static aliases.
        configured = connections.configure_settings(
            {"default": dict(settings.DATABASES["default"]), alias: raw}
        )
        return dict(configured[alias])
