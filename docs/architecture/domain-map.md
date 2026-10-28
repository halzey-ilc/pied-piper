# Domain map

Bounded contexts and their plane (control = global DB; tenant = per-company DB;
shared = table present in every DB).

| Context | Plane | Status | Responsibility |
|---------|-------|--------|----------------|
| identity | control | implemented | Global user accounts, JWT auth, Argon2, account lockout, TOTP MFA, OIDC login |
| organizations | control | implemented | Companies (tenants) and user memberships with roles |
| access_control | (library) | implemented | RBAC: permissions and own/team/all scopes |
| tenant_registry | control | implemented | Tenant→database registry, cluster placement, provisioning jobs, tenant migrations |
| audit | shared | implemented | Append-only audit log |
| customers | tenant | implemented | Customer aggregate — reference implementation of all four layers |
| integrations | tenant | implemented | Webhook ingestion (signature + dedup), typed contracts, channel adapters |
| leads | tenant | implemented | Lead capture, qualify/disqualify, convert to customer |
| sales_pipeline | tenant | implemented | Pipelines, stages, deals, stage moves, win/loss, reopen |
| sales_kpi | tenant | implemented | Event-driven read model, KPI aggregation, snapshots |
| reporting | tenant | implemented | Async report/export jobs (CSV) off the request path |
| activities | tenant | implemented | Tasks, calls, notes on customers/deals, timeline |
| meetings | tenant | implemented | Scheduling, participants, reminders, complete/cancel |
| payments | tenant | implemented | Invoices, payments, void (no card data) |
| telephony | tenant | implemented | Call records, click-to-call, call lifecycle |
| communications | tenant | implemented | Conversations/messages over channels; inbound via webhooks |
| subscriptions | control | implemented | Plans, limits, billing state of organizations |
| notifications | tenant | implemented | In-app notifications from events, per-user preferences |
| ai_insights | tenant | implemented | AI call summaries and operations digests via the separate CRM AI Platform; off by default ([ai-integration](ai-integration.md)) |
| operations | tenant | implemented | Deterministic operations rules (forgotten leads, deals without next step, overdue tasks, stalled deals), per-company thresholds |
| messaging | shared | implemented | Transactional outbox/inbox (platform_core) |

## Context collaboration (events, not calls)

Modules integrate through **domain events** via the outbox, not direct imports
of one another's internals. A module exposes a small `composition.py` and, where
needed, a `composition`-level function for another module's presentation layer
(e.g. identity's `MeView` reads memberships through
`organizations.composition.list_memberships_for_user`).

```
customers --emits--> customers.customer_created / _updated
   -> (outbox relay) -> events queue -> handlers in: sales_kpi (read models),
                                                       notifications, audit
integrations (inbound webhook) -> inbox -> communications / telephony (planned)
tenant_registry.provisioning -> creates tenant DB -> migrates tenant-plane apps
```

## The reference context: `customers`

`customers` is implemented through every layer and is the template for the
others:

- `domain/customer.py` — `Customer` aggregate, value objects, `CustomerCreated`
  / `CustomerUpdated` events. Pure Python.
- `application/service.py` — use cases; enforce RBAC + ownership scope; write the
  outbox and audit inside one unit of work.
- `application/ports.py` — `CustomerRepository` port (a repository is used here
  because the aggregate is deliberately decoupled from the ORM row).
- `infrastructure/` — `CustomerRecord` ORM model, `DjangoCustomerRepository`,
  `composition.py` wiring adapters to the active tenant database.
- `presentation/` — DRF serializers and views on top of `TenantScopedAPIView`.

New contexts copy this shape. A repository is introduced only where the mapping
between aggregate and storage justifies it — not mechanically per model.
