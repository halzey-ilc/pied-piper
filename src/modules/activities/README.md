# Activities

**Status:** implemented (tenant plane).

Tasks, calls and notes attached to a customer or a deal, with a per-subject
timeline.

## Kinds

- **Task** — `title` (required), optional `body` and `due_date`; has a status
  (open / completed) and can be completed or reopened.
- **Call** — `direction` (inbound/outbound) and `occurred_at` (required),
  optional `duration_seconds`, `outcome` and `body`.
- **Note** — `body` (required), optional `title`.

One `Activity` aggregate holds all kinds; kind-specific invariants are enforced
in the domain factory methods, so an activity is always internally consistent.

## Subject

Every activity is attached to a subject: a `customer` or a `deal`. The subject
is verified to exist in the same tenant before the activity is created
(`SubjectChecker` port, backed by the customers and sales_pipeline tables).

## Timeline

`GET /api/v1/activities/?subject_type=customer&subject_id=<id>` returns the
timeline of one customer or deal, newest first. Without a subject it is the
general activity feed, filterable by `kind` and `task_status`.

## Events

`activities.activity_logged`, `activities.task_completed` — written to the
outbox in the same transaction as the change.

## API

`GET/POST /api/v1/activities/`, `GET/PATCH /api/v1/activities/{id}/`,
`POST /api/v1/activities/{id}/complete|reopen/`. All tenant-scoped and
RBAC-checked (`activities.*` permissions, own/team/all scope).
