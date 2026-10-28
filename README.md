# CRM Platform

Multi-tenant CRM backend built as a **modular monolith** (Django 5.2 LTS, DRF,
Celery, PostgreSQL). The architecture follows Clean Architecture / DDD / Ports &
Adapters: each bounded context is split into `domain` → `application` →
`infrastructure` → `presentation`, and the inner layers never import frameworks.

> **Status:** foundation / scaffold. One tenant-plane context (`customers`) is
> implemented end-to-end as a reference; most other contexts are architectural
> stubs. No frontend and no LLM/AI are part of this project. Nothing here is
> production-ready — see [docs/architecture/security-model.md](docs/architecture/security-model.md).

## Multi-tenancy in one paragraph

Global data (users, organizations, memberships, the tenant registry, provisioning
jobs, plans) lives in the **control plane** database. Each company's business data
lives in **its own PostgreSQL database** (the tenant plane). A request names its
tenant via the `X-Tenant` header; the resolver checks membership, activates a
tenant context, and a database router sends tenant-plane models to that tenant's
database. Without a tenant context, tenant data access fails closed. Locally,
all databases live on one PostgreSQL instance; the model supports a database per
company in production. See
[docs/architecture/multi-tenancy.md](docs/architecture/multi-tenancy.md).

## Requirements

- [uv](https://docs.astral.sh/uv/) ≥ 0.13 (manages Python 3.13 and dependencies)
- Docker + Docker Compose (PostgreSQL, Redis, RabbitMQ, API, Celery worker/beat)
- GNU Make (optional; every target has an equivalent `scripts/dev.sh` command)

## Quick start (local)

```bash
# 1. Install the locked toolchain into .venv
make install            # or: uv sync --all-groups

# 2. Create a local .env with generated secrets
make env                # or: ./scripts/init_env.sh

# 3. Start PostgreSQL, Redis, RabbitMQ, the API, a Celery worker and beat
make up                 # or: docker compose up -d --build

# 4. Apply control-plane migrations and create a local tenant database
make migrate            # control plane
make seed               # demo organization + tenant DB (TENANT_PROVISIONING_ENABLED=true)

# API:        http://localhost:8000/api/v1/
# Docs:       http://localhost:8000/api/docs/   (local/test only)
# Liveness:   http://localhost:8000/health/live
# Readiness:  http://localhost:8000/health/ready
```

`make env` generates strong secrets into `.env`; secrets are never printed.

## Tests and quality

```bash
make test           # unit + architecture + api + security (needs only the venv + a throwaway PG for the DB-backed tests)
make test-int       # integration tests: needs the full docker compose stack running
make lint           # ruff
make typecheck      # mypy (strict) + django-stubs
make arch           # import-linter layer contracts
make security       # bandit + pip-audit
make check          # everything above
```

Minimal checks mandated by the brief and where they live:

| Check | Test |
|-------|------|
| Django settings valid | `tests/integration/test_settings_checks.py` |
| API health endpoint | `tests/api/test_health_and_errors.py` |
| PostgreSQL reachable | `tests/integration/test_backing_services.py` |
| Redis reachable | `tests/integration/test_backing_services.py` |
| Celery worker runs a task | `tests/integration/test_backing_services.py` |
| Cross-tenant access forbidden | `tests/security/test_tenant_isolation.py` |
| Config & error handling | `tests/unit/test_env_config.py`, `tests/api/test_health_and_errors.py` |
| Business layer imports no infra | `tests/architecture/test_layers.py` |

Integration tests are marked `@pytest.mark.integration` and **fail** (never skip
silently) when a required service is missing.

## Layout

```
config/            Django project: settings (base/local/test/production), urls, celery, wsgi/asgi
src/
  platform_core/   tenancy runtime, outbox/inbox, observability, API plumbing (no business logic)
  shared/          framework-free shared kernel (value objects, errors, retry)
  modules/<ctx>/   bounded contexts: domain / application / infrastructure / presentation
  entrypoints/     driving adapters: api/ (HTTP routing) and workers/ (Celery tasks)
tests/             unit / integration / api / security / architecture
infrastructure/    docker, postgres, rabbitmq, redis, monitoring, backup configs
docs/              architecture, operations and ADRs
```

## Documentation

- [System overview](docs/architecture/system-overview.md)
- [Domain map](docs/architecture/domain-map.md)
- [Multi-tenancy](docs/architecture/multi-tenancy.md)
- [Security model](docs/architecture/security-model.md)
- [Async processing](docs/architecture/async-processing.md)
- [AI integration](docs/architecture/ai-integration.md) (optional, off by default)
- [Observability](docs/architecture/observability.md)
- [Local development](docs/operations/local-development.md)
- [Backup & restore](docs/operations/backup-restore.md)
- ADRs: [001 stack](docs/adr/ADR-001-technology-stack.md),
  [002 multi-tenant DB](docs/adr/ADR-002-multi-tenant-database-strategy.md),
  [003 modular monolith](docs/adr/ADR-003-modular-monolith.md)
