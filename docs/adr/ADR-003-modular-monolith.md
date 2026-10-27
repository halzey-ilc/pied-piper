# ADR-003: Modular monolith

- **Status:** Accepted
- **Date:** 2026-10-27

## Context

The CRM spans many domains (customers, leads, pipeline, activities, payments,
telephony, communications, reporting, …). We must keep domains decoupled and the
design clean (Clean Architecture, DDD, SOLID, Ports & Adapters) without paying
the operational cost of microservices at the start.

## Decision

Build a **modular monolith**: a single deployable application partitioned into
bounded contexts under `src/modules/<context>/`, each split into
`domain → application → infrastructure → presentation`, with a platform kernel
(`platform_core`) and a framework-free shared kernel (`shared`).

Rules:

- The dependency rule points inward: domain depends on nothing; application
  depends only on domain; infrastructure/presentation depend inward and sit at
  the edges.
- Domain and application layers import **no frameworks** (no Django/DRF/Celery/
  psycopg/redis/httpx, and domain not even Pydantic).
- Modules integrate through **domain events via the outbox**, not by importing
  each other's internals; the one allowed seam is a module's small
  `composition.py`.
- `platform_core` never imports business modules; it reaches them via ports
  configured in settings (dependency inversion).

These rules are **enforced by the build**: `import-linter` contracts in
`pyproject.toml` plus `tests/architecture/test_layers.py` (AST scan for
forbidden imports and a Tarjan cycle check). A violation fails CI.

We deliberately do **not** create a microservice per domain, and we do **not**
auto-generate a generic repository per model — the repository pattern is used
only where the aggregate/storage mapping warrants it (e.g. `customers`).

## Consequences

- One codebase, one deploy, local transactions across a tenant's data, trivial
  refactoring across contexts — while keeping boundaries explicit and checked.
- Boundaries are real enough that a context could later be extracted into a
  service if scale demands it: it already talks to others via events and ports.
- Some ceremony up front (layers, ports, composition) that a flat Django app
  would not have. Accepted: it is what keeps the domains independent and the
  core testable without a database or broker.

## Alternatives rejected

- *Microservices from day one*: premature; adds network failure modes,
  distributed transactions and deployment overhead before the domains are even
  stable.
- *Plain layered Django app*: fastest to start, but business logic leaks into
  views/models and domains couple through the ORM; hard to test in isolation and
  to keep clean as the surface grows.
