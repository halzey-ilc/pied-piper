"""Readiness probes for backing services. Results expose no hosts, versions or errors."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from django.core.cache import cache
from django.db import connections

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProbeResult:
    name: str
    ok: bool


def probe_database(alias: str = "default") -> ProbeResult:
    try:
        with connections[alias].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
        logger.warning(
            "readiness_probe_failed", extra={"probe": "database", "exc_type": type(exc).__name__}
        )
        return ProbeResult("database", ok=False)
    return ProbeResult("database", ok=True)


def probe_cache() -> ProbeResult:
    key = f"health:{uuid.uuid4().hex}"
    try:
        cache.set(key, "1", timeout=5)
        ok = cache.get(key) == "1"
        cache.delete(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "readiness_probe_failed", extra={"probe": "cache", "exc_type": type(exc).__name__}
        )
        return ProbeResult("cache", ok=False)
    return ProbeResult("cache", ok=ok)


def run_readiness_probes() -> list[ProbeResult]:
    return [probe_database(), probe_cache()]
