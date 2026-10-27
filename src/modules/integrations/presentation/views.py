"""Inbound webhook endpoint: POST /api/v1/webhooks/<provider>/<tenant>/

Unauthenticated by design (providers cannot send our JWTs); trust comes from the
provider signature, verified before anything is stored. Unknown provider,
unknown tenant and bad signature produce non-descriptive errors.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from modules.integrations.composition import ingest_webhook, provider_spec
from modules.integrations.domain.providers import (
    IntegrationNotConfiguredError,
    InvalidSignatureError,
    Provider,
)
from platform_core.application.context import current_correlation_id, tenant_scope
from platform_core.infrastructure.observability.metrics import WEBHOOKS
from platform_core.infrastructure.tenancy.services import connection_registry, tenant_resolver
from platform_core.presentation.errors import error_body


class WebhookView(APIView):
    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "webhooks"

    def post(self, request: Request, provider: str, tenant_slug: str) -> Response:
        try:
            provider_enum = Provider(provider)
        except ValueError:
            return Response(error_body("not_found", "Not found."), status=status.HTTP_404_NOT_FOUND)

        body = request.body  # raw bytes: the signature covers the exact payload
        if len(body) > settings.INTEGRATIONS["WEBHOOK_MAX_BODY_BYTES"]:
            WEBHOOKS.labels(provider_enum.value, "too_large").inc()
            return Response(
                error_body("payload_too_large", "Payload too large."),
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        context = tenant_resolver().resolve_for_system(tenant_slug).context
        try:
            with tenant_scope(context):
                result = ingest_webhook().execute(
                    spec=provider_spec(provider_enum),
                    tenant_slug=context.slug,
                    headers=dict(request.headers),
                    body=body,
                    correlation_id=current_correlation_id(),
                )
        except IntegrationNotConfiguredError:
            WEBHOOKS.labels(provider_enum.value, "not_configured").inc()
            return Response(
                error_body("integration_not_configured", "Integration is not configured."),
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except InvalidSignatureError:
            WEBHOOKS.labels(provider_enum.value, "bad_signature").inc()
            return Response(
                error_body("invalid_signature", "Invalid signature."),
                status=status.HTTP_401_UNAUTHORIZED,
            )
        finally:
            connection_registry().release(context.db_alias)

        WEBHOOKS.labels(provider_enum.value, "duplicate" if result.duplicate else "accepted").inc()
        return Response({"status": "accepted", "duplicate": result.duplicate})
