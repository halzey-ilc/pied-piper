# Sales pipeline

**Status:** implemented (tenant plane).

Funnels (pipelines) with ordered stages, and deals that move through them to a
won or lost outcome.

## Model

- **Pipeline** — a named funnel with 1-20 ordered **stages** (name + win
  probability). At most one pipeline per company is the default.
- **Deal** — an opportunity with a title, monetary value (amount + currency),
  owner, optional linked customer and expected close date.

## Deal lifecycle (enforced in the domain)

```
OPEN ──move_to(stage)──> OPEN (another stage of the same pipeline)
  │
  ├──win──> WON
  └──lose(reason)──> LOST

WON / LOST ──reopen──> OPEN
```

Only open deals can be edited or moved. Won/lost deals are read-only until
reopened. Moving to a stage that does not belong to the deal's pipeline is
rejected. A linked `customer_id` is verified to exist in the same tenant
(`CustomerLookup` port).

## Events

`sales.deal_created`, `sales.deal_stage_changed`, `sales.deal_won`,
`sales.deal_lost`, `sales.deal_reopened` — written to the outbox in the same
transaction as the change.

## API

Pipelines: `GET/POST /api/v1/sales/pipelines/`, `GET /api/v1/sales/pipelines/{id}/`.
Deals: `GET/POST /api/v1/sales/deals/`, `GET/PATCH /api/v1/sales/deals/{id}/`,
`POST /api/v1/sales/deals/{id}/stage|win|lose|reopen/`. All tenant-scoped and
RBAC-checked (`pipelines.*`, `deals.*`; deals use own/team/all scope).
