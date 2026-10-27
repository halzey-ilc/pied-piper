"""HTTP middleware: request/correlation ids and request metrics + access log."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from platform_core.application.context import correlation_scope, sanitize_id
from platform_core.infrastructure.observability.metrics import HTTP_LATENCY, HTTP_REQUESTS

logger = logging.getLogger("crm.access")

REQUEST_ID_HEADER = "X-Request-ID"
CORRELATION_ID_HEADER = "X-Correlation-ID"


class RequestContextMiddleware:
    """Assigns a request id (inbound ids are accepted only if log-safe) and a correlation id."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        inbound_rid = sanitize_id(request.headers.get(REQUEST_ID_HEADER))
        inbound_cid = sanitize_id(request.headers.get(CORRELATION_ID_HEADER))
        with correlation_scope(request_id=inbound_rid, correlation_id=inbound_cid) as (rid, cid):
            response = self.get_response(request)
            response[REQUEST_ID_HEADER] = rid
            response[CORRELATION_ID_HEADER] = cid
            return response


class MetricsMiddleware:
    """Records latency per route *template* (never the raw path) and logs one access line."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        started = time.perf_counter()
        response = self.get_response(request)
        elapsed = time.perf_counter() - started
        match = getattr(request, "resolver_match", None)
        route = match.route if match is not None and match.route else "unmatched"
        HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        HTTP_LATENCY.labels(request.method, route).observe(elapsed)
        if not route.startswith(("health/", "metrics")):
            logger.info(
                "http_request",
                extra={
                    "method": request.method,
                    "route": route,
                    "status": response.status_code,
                    "duration_ms": round(elapsed * 1000, 2),
                },
            )
        return response
