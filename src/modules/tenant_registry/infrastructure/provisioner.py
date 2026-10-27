"""Creates tenant databases on a PostgreSQL cluster.

Identifiers are validated against a strict pattern AND quoted with
psycopg.sql.Identifier; nothing user-controlled is interpolated into SQL.
The role used here needs CREATEDB (local development). In production use a
dedicated provisioning role, separate from the application role.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import psycopg
from psycopg import sql

from platform_core.domain.tenancy import validate_database_name


class PostgresDatabaseProvisioner:
    def __init__(self, clusters: Mapping[str, Mapping[str, Any]]) -> None:
        self._clusters = clusters

    def _connect(self, cluster: str) -> psycopg.Connection[Any]:
        cfg = self._clusters[cluster]
        options = cfg.get("OPTIONS", {})
        return psycopg.connect(
            host=cfg["HOST"],
            port=cfg["PORT"],
            user=cfg["USER"],
            password=cfg["PASSWORD"],
            dbname="postgres",
            connect_timeout=options.get("connect_timeout", 5),
            sslmode=options.get("sslmode", "prefer"),
            autocommit=True,  # CREATE DATABASE cannot run inside a transaction
        )

    def create_database(self, cluster: str, database_name: str) -> bool:
        name = validate_database_name(database_name)
        owner = self._clusters[cluster]["USER"]
        with self._connect(cluster) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
            ).fetchone()
            if exists:
                return False
            conn.execute(
                sql.SQL("CREATE DATABASE {} OWNER {} TEMPLATE template0 ENCODING 'UTF8'").format(
                    sql.Identifier(name), sql.Identifier(owner)
                )
            )
            conn.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name))
            )
        return True
