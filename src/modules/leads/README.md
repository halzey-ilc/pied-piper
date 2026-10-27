# Leads

**Status:** implemented (tenant plane).

Capture, qualify/disqualify and convert potential customers.

## Lifecycle

```
NEW ──qualify──> QUALIFIED ──convert──> CONVERTED (terminal)
  │                  │
  └──disqualify──────┴──> UNQUALIFIED ──qualify──> QUALIFIED
```

Transitions are enforced in the domain (`domain/lead.py`), not in the API.

## Conversion

Converting a qualified lead creates a customer through the `customers` context
(`CustomerCreation` port) on the same tenant database, inside one transaction,
and records `converted_customer_id` on the lead. Conversion is idempotent:
converting an already-converted lead returns the existing customer id.

## Events

`leads.lead_captured`, `leads.lead_qualified`, `leads.lead_disqualified`,
`leads.lead_converted` — written to the outbox in the same transaction as the
change.

## API

`GET/POST /api/v1/leads/`, `GET/PATCH /api/v1/leads/{id}/`,
`POST /api/v1/leads/{id}/qualify|disqualify|convert/`. All tenant-scoped
(`X-Tenant` header) and RBAC-checked (`leads.*` permissions, own/team/all scope).
