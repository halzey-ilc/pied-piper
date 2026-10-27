# ADR-002: Multi-tenant database strategy

- **Status:** Accepted
- **Date:** 2026-10-27

## Context

The CRM serves many companies. Their data must be strongly isolated (a breach or
a forgotten filter must not leak one company's data to another), individually
backup/restorable, and operable at scale. Three common models:

1. **Shared schema, `tenant_id` column** on every table.
2. **Schema per tenant** in one database.
3. **Database per tenant** (optionally across clusters), with a separate
   control-plane database for global data.

## Decision

Adopt **database per tenant** with a separate **control plane**:

- Control-plane database: users, organizations, memberships, the tenant
  registry, provisioning jobs, plans/limits.
- One tenant database per company for business data.
- Shared tables (outbox, inbox, audit) exist in every database so events and
  audit are written in the same transaction as the data they describe.

Routing is runtime and fail-closed (see
[multi-tenancy](../architecture/multi-tenancy.md)); connection aliases are
registered lazily and bounded by an LRU; placement chooses a cluster by free
capacity; the registry stores only cluster key + database name, never
credentials.

For local development, the control-plane and tenant databases live on one
PostgreSQL instance. This is a convenience, not the production model.

## Rationale

- **Isolation by construction.** A query in the wrong tenant context physically
  cannot see another tenant's rows; isolation does not depend on remembering a
  `WHERE tenant_id = ?` on every query. This directly satisfies the brief's
  requirement that a single view-level check is *not* considered sufficient.
- **Per-tenant operations.** Backup, restore, point-in-time recovery, data
  export and deletion (GDPR) are per database.
- **Blast radius & scaling.** Tenants spread across clusters; a heavy tenant can
  be moved; one corrupt database does not implicate others.

## Trade-offs / costs

- **Migrations fan out** across many databases. Mitigated by `migrate_tenants`
  (sequential, one connection at a time) and recorded `schema_version` with a
  `--check` drift report.
- **Connection pressure.** Mitigated by bounded lazy aliases + PgBouncer
  (transaction pooling); never open all tenant DBs at once.
- **Provisioning is a real workflow,** not a row insert. Mitigated by an
  idempotent, job-tracked provisioning use case, disabled by default.
- **Cross-tenant analytics** is harder than in a shared schema; addressed later
  by read models / an analytics store, not by querying all tenant DBs live.

## Rejected alternatives

- *Shared schema + `tenant_id`*: cheapest, but isolation rests entirely on query
  discipline and row-level security; a single missed filter leaks data. Rejected
  for a CRM holding customers' customer data.
- *Schema per tenant*: better than shared, but still one database — coarse
  backup/restore granularity and a single failure domain. Rejected in favour of
  the stronger database boundary.
