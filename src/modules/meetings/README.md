# Meetings

**Status:** implemented (tenant plane).

Scheduling with participants and reminders, optionally linked to a customer or
deal.

## Model

A `Meeting` aggregate holds its **participants** and **reminders** as value
objects (persisted as JSON on the same row, since they belong to the meeting):

- **Participant** — name (required), optional e-mail and internal user id, and a
  response (`needs_action` / `accepted` / `declined`). Deduplicated by e-mail,
  else by name.
- **Reminder** — minutes before the start (0 … 4 weeks), unique, kept sorted.

## Lifecycle (enforced in the domain)

```
SCHEDULED ──reschedule / edit / add participant / respond──> SCHEDULED
    ├──complete(outcome)──> COMPLETED
    └──cancel(reason)─────> CANCELLED
```

Only scheduled meetings can be changed. `scheduled_end` must be after
`scheduled_start` and within 24 hours. A linked customer/deal is verified to
exist in the same tenant (`SubjectChecker` port).

## Events

`meetings.meeting_scheduled`, `meetings.meeting_rescheduled`,
`meetings.meeting_completed`, `meetings.meeting_cancelled` — written to the
outbox in the same transaction as the change.

> Reminder **delivery** (sending the actual notification) is out of scope here;
> reminders are stored and surfaced. Delivery belongs to the notifications
> module (planned) consuming these events.

## API

`GET/POST /api/v1/meetings/`, `GET/PATCH /api/v1/meetings/{id}/`,
`POST /api/v1/meetings/{id}/reschedule|complete|cancel/`,
`POST /api/v1/meetings/{id}/participants/` and `.../participants/respond/`.
All tenant-scoped and RBAC-checked (`meetings.*`, own/team/all scope).
