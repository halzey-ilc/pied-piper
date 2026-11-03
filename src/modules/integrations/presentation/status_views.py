from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.integrations.application.status import ProviderStatus
from modules.integrations.composition import integration_status_service
from modules.integrations.domain.providers import Provider
from platform_core.presentation.tenancy import TenantScopedAPIView
from shared.domain.errors import NotFoundError

_TENANT_HEADER = OpenApiParameter(
    "X-Tenant",
    str,
    OpenApiParameter.HEADER,
    required=True,
    description="Short name (slug) of your company, e.g. `demo`.",
    examples=[OpenApiExample("Demo tenant", value="demo")],
)
_TAGS = ["Integrations"]


class _Scoped(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
            department_id=self.membership.department_id,
        )


def _status_to_dict(s: ProviderStatus) -> dict[str, Any]:
    return {
        "provider": s.provider.value,
        "title": s.title,
        "inbound_configured": s.inbound_configured,
        "webhook_path": s.webhook_path,
        "outbound_connected": s.outbound_connected,
        "received_total": s.stats.received_total,
        "received_last_24h": s.stats.received_last_24h,
        "failed_total": s.stats.failed_total,
        "last_received_at": s.stats.last_received_at.isoformat()
        if s.stats.last_received_at
        else None,
        "recent_errors": [
            {"received_at": e.received_at.isoformat(), "status": e.status, "error": e.error}
            for e in s.stats.recent_errors
        ],
    }


class IntegrationListView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="integrations_status",
        summary="Channel connections of the company",
        description=(
            "Per channel (WhatsApp, Telegram, Instagram, telephony): whether inbound events "
            "are accepted (the webhook secret is set on the server), the company's webhook "
            "address, whether the CRM can send through the channel (no outbound adapter is "
            "connected in this installation: always false), counts of received events and "
            "the latest processing errors. Needs integrations.manage (director)."
        ),
        parameters=[_TENANT_HEADER],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request) -> Response:
        statuses = integration_status_service().statuses(self.principal(), datetime.now(UTC))
        return Response({"results": [_status_to_dict(s) for s in statuses]})


class IntegrationCheckView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="integrations_check",
        summary="Check a channel connection",
        description=(
            "Runs the checks possible from inside the CRM — webhook secret, event store, "
            "outbound sending — and states what cannot be checked without the provider. "
            "Each item has `ok` and a message in Russian."
        ),
        parameters=[_TENANT_HEADER],
        request=None,
        responses=OpenApiTypes.OBJECT,
    )
    def post(self, request: Request, provider: str) -> Response:
        try:
            provider_enum = Provider(provider)
        except ValueError as exc:
            raise NotFoundError("Unknown channel.") from exc
        items = integration_status_service().check(self.principal(), provider_enum)
        return Response(
            {
                "provider": provider_enum.value,
                "ok": all(i.ok for i in items if i.name in {"inbound_secret", "event_store"}),
                "items": [{"name": i.name, "ok": i.ok, "message": i.message} for i in items],
            }
        )
