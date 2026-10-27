# Monitoring

Instrumentation is built in; the backends are optional and off by default.

- **Metrics (Prometheus):** the API serves `/metrics` (Bearer `METRICS_TOKEN`);
  the Celery worker serves `:9100` when `CELERY_METRICS_PORT` is set. Both use
  `prometheus_client` multiprocess mode (`PROMETHEUS_MULTIPROC_DIR`). See
  [`prometheus.yml`](prometheus.yml) for a scrape example.
- **Dashboards (Grafana):** import a dashboard that charts the metric families
  `crm_http_request_duration_seconds`, `crm_celery_task_duration_seconds`,
  `crm_outbox_events_total`, `crm_webhooks_total`. A provisioned dashboard JSON
  is a follow-up (see `docs/architecture/observability.md`).
- **Tracing (OpenTelemetry):** enable with `OTEL_ENABLED=true` and
  `OTEL_EXPORTER_OTLP_ENDPOINT`. Django, psycopg and Celery are instrumented;
  request/response bodies and SQL parameters are not captured.
- **Errors (Sentry):** set `SENTRY_DSN`. PII is off and events are scrubbed.

Running Prometheus and Grafana as local containers is a documented next step,
intentionally not added to the default stack to keep it lean.
