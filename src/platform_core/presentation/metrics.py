"""Prometheus scrape endpoint.

With METRICS_TOKEN set, a matching ``Authorization: Bearer`` header is required.
Without a token the endpoint is served only in local/test environments
(production configuration refuses to start without METRICS_TOKEN).
"""

from __future__ import annotations

import hmac

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from platform_core.infrastructure.observability.metrics import render_latest


@never_cache
@require_GET
def metrics_view(request: HttpRequest) -> HttpResponse:
    token = settings.METRICS_TOKEN
    if token is None:
        if settings.OBSERVABILITY["ENVIRONMENT"] == "production":
            raise Http404
    else:
        supplied = request.headers.get("Authorization", "")
        expected = f"Bearer {token.get_secret_value()}"
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            raise Http404
    body, content_type = render_latest()
    return HttpResponse(body, content_type=content_type)
