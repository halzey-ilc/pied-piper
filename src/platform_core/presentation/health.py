"""Liveness / readiness endpoints (unauthenticated, minimal output)."""

from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from platform_core.infrastructure.health import run_readiness_probes


@never_cache
@require_GET
def liveness(request: HttpRequest) -> JsonResponse:
    """The process is up and serving requests. Checks no dependencies."""
    return JsonResponse({"status": "ok"})


@never_cache
@require_GET
def readiness(request: HttpRequest) -> JsonResponse:
    """Ready to take traffic: control-plane database and cache reachable."""
    results = run_readiness_probes()
    ready = all(result.ok for result in results)
    return JsonResponse(
        {
            "status": "ok" if ready else "unavailable",
            "checks": {result.name: "ok" if result.ok else "unavailable" for result in results},
        },
        status=200 if ready else 503,
    )
