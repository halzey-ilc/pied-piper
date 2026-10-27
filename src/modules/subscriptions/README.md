# Subscriptions

**Status:** implemented (**control plane**).

Each organization has one subscription to a plan. Unlike the other business
modules, this lives in the **control-plane** database (it is global data about a
company, not that company's business data), so its repository always uses the
`default` database and the app is classified under `CONTROL_PLANE_APPS`.

## Plans

A static catalog (`domain/plan.py`): **Free**, **Pro**, **Enterprise**, each with
a monthly price and limits (`users`, `customers`, `deals`; `-1` = unlimited).
`Plan.allows(metric, current)` answers whether a usage count is within the plan.

## Lifecycle

```
(first access) -> ACTIVE(Free)
ACTIVE ──change_plan──> ACTIVE(new plan)
ACTIVE ──mark_past_due──> PAST_DUE
ACTIVE / PAST_DUE ──cancel──> CANCELLED ──reactivate──> ACTIVE
```

## Events

`subscriptions.subscription_started`, `subscriptions.plan_changed`,
`subscriptions.subscription_cancelled` — written to the control-plane outbox.

## API

The caller's organization is selected by the `X-Tenant` header (membership
checked), and the subscription is read/written in the control plane.

`GET /api/v1/subscription/`, `POST /api/v1/subscription/` (change plan),
`GET /api/v1/subscription/plans/`, `POST /api/v1/subscription/cancel|reactivate/`.
`subscriptions.view` to read; `subscriptions.manage` (owner/admin) to change.
