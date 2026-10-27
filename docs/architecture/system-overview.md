# System overview

CRM Platform is a **modular monolith**: one deployable Django application
partitioned into bounded contexts, with a strict dependency rule between layers.
It is not a set of microservices; see
[ADR-003](../adr/ADR-003-modular-monolith.md).

## Processes

| Process | Role |
|---------|------|
| API (gunicorn/WSGI) | Serves the REST API, health and metrics endpoints |
| Celery worker | Outbox relay, event dispatch, webhook processing, reports, notifications |
| Celery beat | Scheduled jobs (outbox relay every 15s, future daily reports) |
| PostgreSQL | Control-plane DB + one DB per tenant |
| Redis | Django cache and Celery result backend (never the source of truth) |
| RabbitMQ | Celery broker with per-class queues and dead-letter queues |

## Layers (the dependency rule)

```
presentation  ->  application  ->  domain
     |                |
     +--------------- infrastructure ------+
```

- **domain** — entities, value objects, domain rules and events. Pure Python:
  no Django, DRF, Celery, Redis, psycopg, httpx, even Pydantic.
- **application** — use cases, commands/queries, ports (Protocols), transaction
  scenarios. No HTTP, no ORM.
- **infrastructure** — Django ORM, repositories, brokers, cache, external
  adapters. Implements the application's ports.
- **presentation** — DRF views, serializers, permissions, routing. No business
  logic.

The rule is enforced automatically: `import-linter` contracts in
`pyproject.toml` and `tests/architecture/test_layers.py` (AST scan + cycle
detection) fail the build on violations.

## Platform kernel

`platform_core` provides cross-cutting machinery used by every module — tenant
routing, the transactional outbox/inbox, observability, API error handling — and
**never imports business modules**. It reaches them only through ports resolved
from settings (`TENANCY["DIRECTORY"]`, `TENANCY["MEMBERSHIPS"]`), i.e.
dependency inversion. `shared` is a framework-free kernel (value objects, errors,
retry policy) that does not even import `platform_core`.

## Request lifecycle (tenant-scoped endpoint)

1. `RequestContextMiddleware` assigns request/correlation ids (inbound ids are
   accepted only if log-safe).
2. JWT authentication identifies the user.
3. `TenantScopedAPIView` resolves the `X-Tenant` header → checks membership →
   activates a tenant context (fail closed if unknown / not a member → 404).
4. The view calls an application service; the service enforces RBAC and runs the
   use case in a unit of work on the tenant's database.
5. Domain events are written to that database's outbox in the same transaction.
6. `MetricsMiddleware` records latency by route template; the tenant connection
   is released at the end of dispatch.

## What exists today

Implemented end to end: identity (JWT), organizations/memberships, tenant
registry + provisioning, access control (RBAC), audit, the `customers`
tenant-plane context, and webhook ingestion. KPI/reporting are domain + contracts
only. Other contexts (`leads`, `sales_pipeline`, `activities`, `meetings`,
`payments`, `telephony`, `communications`, `subscriptions`, `notifications`) are
documented stubs. No frontend, no LLM/AI.
