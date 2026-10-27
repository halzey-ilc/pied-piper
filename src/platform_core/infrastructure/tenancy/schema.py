"""Tenant schema versioning.

The schema version of a tenant database is a short hash of the latest applied
migration of every tenant-plane and shared app. The *expected* version is the
same hash computed from the migration graph leaf nodes on disk, so drift can be
detected without opening every tenant database at once.
"""

from __future__ import annotations

import hashlib

from django.db import connections
from django.db.migrations.loader import MigrationLoader

from platform_core.infrastructure.tenancy.router import plane_for


def _fingerprint(nodes: list[tuple[str, str]]) -> str:
    joined = ",".join(f"{app}.{name}" for app, name in sorted(nodes))
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def _tenant_plane(app_label: str) -> bool:
    return plane_for(app_label) in ("tenant", "shared")


def expected_schema_version() -> str:
    loader = MigrationLoader(None, ignore_no_migrations=True)
    leaves = [node for node in loader.graph.leaf_nodes() if _tenant_plane(node[0])]
    return _fingerprint(leaves)


def applied_schema_version(alias: str) -> str:
    loader = MigrationLoader(connections[alias], ignore_no_migrations=True)
    latest: dict[str, str] = {}
    for app_label, name in loader.applied_migrations:
        if not _tenant_plane(app_label):
            continue
        if app_label not in latest or name > latest[app_label]:
            latest[app_label] = name
    return _fingerprint(list(latest.items()))
