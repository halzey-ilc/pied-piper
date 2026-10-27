"""Process-level observability start-up: Sentry (only with a DSN) and OpenTelemetry
(only when OTEL_ENABLED). Both are opt-in; nothing is sent anywhere by default."""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

from platform_core.infrastructure.observability.logging import SENSITIVE_KEY_RE, redact_value

logger = logging.getLogger(__name__)
_initialised: set[str] = set()


def _scrub_sentry_event(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("data", None)
        request.pop("cookies", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                k: ("[REDACTED]" if SENSITIVE_KEY_RE.search(k) else v) for k, v in headers.items()
            }
    extra = event.get("extra")
    if isinstance(extra, dict):
        event["extra"] = {k: redact_value(k, v) for k, v in extra.items()}
    return event


def _init_sentry(config: dict[str, Any], component: str) -> None:
    dsn = config.get("SENTRY_DSN")
    if dsn is None:
        return
    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn.get_secret_value(),
        environment=config["ENVIRONMENT"],
        send_default_pii=False,
        traces_sample_rate=config["SENTRY_TRACES_SAMPLE_RATE"],
        before_send=_scrub_sentry_event,  # type: ignore[arg-type]
        server_name=component,
    )


def _init_otel(config: dict[str, Any], component: str) -> None:
    if not config.get("OTEL_ENABLED"):
        return
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create(
        {
            "service.name": config["SERVICE_NAME"],
            "service.namespace": "crm",
            "deployment.environment": config["ENVIRONMENT"],
            "crm.component": component,
        }
    )
    provider = TracerProvider(resource=resource)
    endpoint = config.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    exporter = OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces") if endpoint else None
    if exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    # No request/response header or body capture is configured; SQL parameters
    # are not recorded by the psycopg instrumentation.
    DjangoInstrumentor().instrument(excluded_urls="health/.*,metrics")
    PsycopgInstrumentor().instrument(enable_commenter=False)
    CeleryInstrumentor().instrument()  # type: ignore[no-untyped-call]


def init_observability(component: str) -> None:
    if component in _initialised:
        return
    _initialised.add(component)
    config: dict[str, Any] = settings.OBSERVABILITY
    _init_sentry(config, component)
    _init_otel(config, component)
    logger.info(
        "observability_initialised",
        extra={
            "component": component,
            "sentry": config.get("SENTRY_DSN") is not None,
            "otel": bool(config.get("OTEL_ENABLED")),
        },
    )
