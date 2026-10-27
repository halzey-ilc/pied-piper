# Async processing

Celery on RabbitMQ (broker) with Redis as the result backend. Business logic
lives in application services; Celery tasks in `entrypoints/workers` are thin
adapters.

## Queues

Separate queues per task class (`config.settings.CELERY_TASK_QUEUES` built from
`platform_core.infrastructure.messaging.queues`):

`default`, `outbox`, `events`, `integrations`, `notifications`, `reports`,
`diagnostics`.

Each work queue is declared durable with a dead-letter exchange routing to
`<queue>.dlq`. `CELERY_TASK_CREATE_MISSING_QUEUES=false` — routing is explicit.

## Reliability settings

- `task_acks_late=true` + `reject_on_worker_lost=true`: a task is acked only
  after it finishes, so a worker crash redelivers it. **Tasks must be
  idempotent.**
- `acks_on_failure_or_timeout=false`: a task that finally fails is rejected
  (requeue=false) and RabbitMQ dead-letters it.
- `prefetch_multiplier=1`, `max_tasks_per_child=1000`.
- Time limits: soft/hard from `CELERY_TASK_*_TIME_LIMIT`.
- Publisher confirms (`confirm_publish`) so a publish that the broker did not
  accept raises.
- Retries: `ResilientTask` retries `TransientError` with exponential backoff +
  jitter, bounded, then dead-letters. `PermanentError` is never retried.

## Transactional outbox

Business changes and the events they emit are written in **one transaction, in
the same database** (control plane or a tenant DB): `OutboxMessage` rows next to
the business rows. A Celery beat job (`platform.outbox.relay_all`, every 15s)
relays due rows:

- `SELECT ... FOR UPDATE SKIP LOCKED` claims a batch (safe with multiple
  relays);
- publish to the `events` queue; on success mark published;
- on failure schedule a retry with backoff, or dead-letter after the cap.

Guarantee: **at-least-once** delivery from outbox to broker (a crash between
publish and mark-published republishes). Consumers therefore deduplicate.

The relay visits the control plane and each active tenant database
**sequentially**, releasing each tenant connection before the next; one broken
tenant DB does not stop the others.

## Inbox (idempotent consumption)

Inbound events and webhooks are recorded in `InboxMessage` with a unique
constraint on `(source, message_id, consumer)`. `IdempotentConsumer` runs a
handler and writes the inbox row in one transaction, so:

- a duplicate (provider retry, redelivered event) is detected and skipped;
- a failing handler rolls back its inbox row too, so the message can be retried.

## Correlation

`correlation_id` flows from the HTTP request into tasks (via message headers and
`contextvars`) and into every log line, so one logical operation is traceable
across API and workers.

## What is proven vs. asserted

`tests/unit/test_messaging_and_retry.py` proves relay/retry/idempotency logic
with fakes; `tests/integration/test_outbox_inbox_db.py` proves atomicity,
the inbox constraint and relay state transitions against real PostgreSQL;
`tests/integration/test_backing_services.py` proves a worker runs a task and
that a finally-failed task reaches its dead-letter queue. End-to-end delivery
guarantees under adverse broker conditions beyond these cases are **not** yet
proven.
