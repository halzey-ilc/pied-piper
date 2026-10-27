# Multi-tenancy

**Model: database per tenant.** Each company's business data lives in its own
PostgreSQL database. Global data lives in a separate control-plane database.
See [ADR-002](../adr/ADR-002-multi-tenant-database-strategy.md) for why.

## Control plane vs tenant plane

| Plane | Database | Contains |
|-------|----------|----------|
| Control | `crm_control` | users, organizations, memberships, tenant registry, provisioning jobs, (plans/limits) |
| Tenant | `crm_tenant_<slug>` (one each) | customers and all other business data for that company |
| Shared tables | every database | outbox, inbox, audit |

Locally this is one PostgreSQL instance holding the control-plane database and
the tenant databases. In production the same model spreads tenant databases
across one or more clusters — it is **not** one shared database for all
customers.

## Runtime routing

- `platform_core.application.context` holds the active tenant in a `ContextVar`
  (isolated per thread/async task).
- `platform_core.infrastructure.tenancy.router.TenantRouter` routes each model
  by its app's plane:
  - control-plane apps → always `default`;
  - tenant-plane apps → the active tenant's alias, and **raise** if no tenant
    context is active (fail closed — no silent fallback to another database);
  - shared apps → the active tenant if present, else `default`.
  It also rejects using an object loaded from one tenant database under a
  different tenant context (`CrossTenantAccessError`).
- `DjangoTenantConnectionRegistry` registers database aliases **lazily** and
  keeps at most `TENANT_MAX_ACTIVE_ALIASES` per process (LRU eviction). We never
  open connections to all tenant databases at once. Credentials come from
  server-side `DATABASE_CLUSTERS` config; the registry row only stores a cluster
  key and database name — never a connection string from a request.

## Tenant resolution and isolation

A request carries `X-Tenant: <slug>`. `TenantResolver`:

1. validates the slug, looks it up in the registry, requires status ACTIVE;
2. requires the caller to have a membership in that tenant;
3. unknown tenant, inactive tenant and non-member all raise the **same**
   `TenantNotFoundError` → HTTP 404, so tenants cannot be enumerated.

Isolation is defense-in-depth, not a single header check:

1. membership gate (above);
2. physical separation — tenant data is in a different database, so a query in
   the wrong context cannot see it even if a filter is forgotten;
3. router fail-closed without a context;
4. RBAC + ownership scope inside each use case.

`tests/security/test_tenant_isolation.py` proves a user of one company can
neither read nor modify another company's data across all of these.

## Provisioning

`modules.tenant_registry` provisions a tenant database idempotently:
**register → create database → migrate tenant-plane apps → activate**, tracked by
a `ProvisioningJob` (a partial unique index allows only one in-flight job per
organization). It is **disabled** unless `TENANT_PROVISIONING_ENABLED=true` and
never runs automatically. Cluster placement picks the open cluster with the most
free capacity. Database names are derived deterministically and validated against
a strict pattern, then created with `psycopg.sql.Identifier` quoting.

Commands:

```bash
python manage.py provision_tenant <slug> --create-organization --name "..."
python manage.py migrate_tenants            # migrate all active tenants, one at a time
python manage.py migrate_tenants --check    # report schema drift, change nothing
```

`manage.py migrate` (plain) migrates the control plane only.

## Schema versioning

Each tenant database records a `schema_version` — a hash of the latest applied
migration per tenant-plane/shared app. `migrate_tenants --check` compares it to
the expected version computed from the migration graph on disk, detecting drift
without opening every tenant database simultaneously.

## Connection management & PgBouncer

`CONN_MAX_AGE=0` for tenant aliases (connections close at end of
request/task); pooling is delegated to **PgBouncer** in transaction mode
(`DB_USE_PGBOUNCER=true` disables server-side cursors and prepared-statement
reuse accordingly). This keeps the number of simultaneously open connections
bounded regardless of tenant count.
