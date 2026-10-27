# ADR-001: Technology stack

- **Status:** Accepted
- **Date:** 2026-10-27

## Context

We need a backend foundation for a multi-tenant CRM: strong data modelling, a
mature ORM and migrations, first-class async processing, and a typed, testable
codebase. The brief lists a target stack and asks us to pin exact, compatible
versions and to document any deviation.

## Decision

Python **3.13** managed by **uv**, with dependencies pinned in `uv.lock` and
`pyproject.toml`. Core runtime libraries (resolved versions):

| Area | Choice | Version |
|------|--------|---------|
| Language | CPython (via uv) | 3.13.18 |
| Web framework | Django (LTS) | 5.2.18 |
| API | Django REST Framework | 3.18.3 |
| Auth | djangorestframework-simplejwt | 5.5.1 |
| API schema | drf-spectacular | 0.29.0 |
| DB driver | psycopg (v3, binary) | 3.3.6 |
| Async | Celery | 5.6.3 |
| Broker | RabbitMQ (`rabbitmq:4`) + kombu | 5.6.x |
| Cache / results | Redis (`redis:7.4`) + redis-py | 7.4.1 |
| Validation | Pydantic (+ pydantic-settings) | 2.14.1 |
| HTTP client | HTTPX | 0.28.1 |
| Passwords | argon2-cffi | 25.1.0 |
| WSGI server | gunicorn | 23.0.0 |
| Metrics / tracing / errors | prometheus-client, OpenTelemetry, sentry-sdk | 0.26 / 1.45 / 2.73 |
| Database | PostgreSQL | 17 |

Dev/quality toolchain: pytest + pytest-django, Ruff, mypy (strict) with
django-stubs/djangorestframework-stubs, Bandit, pip-audit, import-linter.
Containerised with a multi-stage Dockerfile and Docker Compose; CI on GitHub
Actions.

## Deviations from the brief and why

- **Python 3.13, not 3.13+-or-3.14.** The host had only Python 3.14 and no
  working `venv`/`pip`. 3.14 is too new for parts of the pinned stack (notably
  the mypy Django stubs matrix). We installed uv (user-local, no system changes)
  and used its managed **CPython 3.13.18**, which every dependency supports.
- **DRF runtime 3.18.3, type stubs kept on the 3.16 line.** We initially pinned
  DRF 3.16 because `djangorestframework-stubs` 3.18 requires `django-stubs >= 6.1`
  (which targets Django 6.x), while we stay on Django 5.2 LTS. `pip-audit` then
  flagged CVEs against DRF 3.16.1 (fixed in 3.18.2), so the **runtime** is pinned
  to DRF 3.18.3 for the security fix, while the **dev-only** type stubs stay at
  `djangorestframework-stubs` 3.16.9 + `django-stubs` 5.2.9 to match Django 5.2.
  The minor stub/runtime skew is benign for the DRF surface we use, and mypy
  (strict) passes. Revisit the stubs when django-stubs supports a Django 6 LTS.
- **pytest 9.** Bumped from 8.4.2 to 9.1.1 to clear a pytest advisory
  (PYSEC-2026-1845); pytest-django 4.14 supports pytest 9. After these bumps
  `pip-audit` reports no known vulnerabilities.
- **ClickHouse / OpenSearch not included.** The brief defers these; analytics
  ships as domain contracts only.
- **pgBouncer / pgBackRest / Prometheus / Grafana** are configured and
  documented but not run in the default local stack, to keep it lean. The
  settings support them (`DB_USE_PGBOUNCER`, metrics endpoints).

## Consequences

- Reproducible installs from `uv.lock`; one command to recreate the environment.
- Staying on Django 5.2 LTS buys stability and a clean strict-mypy setup at the
  cost of not using the newest DRF. Revisit when the stubs support Django 6 LTS.
- No system packages were installed; the toolchain is user-local and removable.
