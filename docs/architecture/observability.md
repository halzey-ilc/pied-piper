# Observability

Three pillars, all built in; exporters are opt-in and silent by default.

## Logging

Structured JSON (`platform_core.infrastructure.observability.logging`). Every
line carries `request_id`, `correlation_id` and `tenant`. Two filters run on
every record:

- `ContextFilter` injects the context ids;
- `RedactionFilter` removes sensitive data — keys matching
  password/secret/token/authorization/cookie/api-key/signature/dsn/… and,
  deliberately, customer message content and audio — plus URL credentials and
  Bearer tokens embedded in message text.

`LOG_JSON=false` switches to a console format for local readability.

## Metrics (Prometheus)

`platform_core.infrastructure.observability.metrics` defines bounded-cardinality
metrics (route templates and task names as labels — never tenant ids):

- `crm_http_requests_total`, `crm_http_request_duration_seconds`
- `crm_celery_tasks_total`, `crm_celery_task_duration_seconds`
- `crm_outbox_events_total`, `crm_webhooks_total`

The API serves `/metrics`, guarded by a Bearer `METRICS_TOKEN` (and hidden
entirely in production without one). The worker serves metrics on
`CELERY_METRICS_PORT`. Multiprocess mode aggregates across gunicorn/worker
processes via `PROMETHEUS_MULTIPROC_DIR`.

## Tracing (OpenTelemetry)

Off unless `OTEL_ENABLED=true`. When on, Django, psycopg and Celery are
instrumented and spans export to `OTEL_EXPORTER_OTLP_ENDPOINT` (OTLP/HTTP).
Health and metrics URLs are excluded; request/response headers and bodies and
SQL parameters are **not** captured.

## Errors (Sentry)

Initialised only when `SENTRY_DSN` is set. `send_default_pii=False` and a
`before_send` scrubber drops request bodies/cookies and redacts sensitive
headers and extra fields.

## Health & readiness

- `GET /health/live` — process is up; checks no dependencies.
- `GET /health/ready` — control-plane database and cache reachable; returns 503
  when a dependency is down. Output never leaks hosts, versions or error text.

## Recommendations

Run Prometheus + Grafana alongside the stack (see
`infrastructure/monitoring/`), scrape the API and worker, and alert on: 5xx rate
and p99 latency per route, task failure/retry rate and duration per task,
`crm_outbox_events_total{result="failed"}` growth (relay falling behind), and
`crm_webhooks_total{result="bad_signature"}` spikes. A provisioned Grafana
dashboard is a documented next step.
