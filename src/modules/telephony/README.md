# Telephony

**Status:** implemented (tenant plane).

Call records and click-to-call, linked to customers or deals.

**No audio or transcripts are stored or logged.** Recordings are referenced by
URL only.

## Two ways a call appears

- **Logged** — a call that already happened, recorded with its numbers (E.164),
  direction, start time, duration and terminal status (completed/missed/failed).
- **Click-to-call** — an outbound call started through a provider dialer
  (`CallDialer` port). In the scaffold the dialer is a stub that returns a
  synthetic id; a real adapter would place the call through the provider.

## Lifecycle (click-to-call)

```
REQUESTED -> RINGING -> IN_PROGRESS -> (COMPLETED | MISSED | FAILED)
```

Progress is driven by `advance` (ringing/in_progress) and `end` (terminal). In
production these would be updated by provider webhooks (integrations).

## Events

`telephony.call_started`, `telephony.call_ended` — written to the outbox in the
same transaction.

## API

`GET/POST /api/v1/telephony/calls/`, `POST /api/v1/telephony/calls/request/`,
`GET /api/v1/telephony/calls/{id}/`, `POST .../{id}/advance|end/`. Tenant-scoped,
RBAC (`telephony.*`, own/team/all scope).
