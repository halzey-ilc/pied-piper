"""Sipuni: the director's connection settings and the public event address."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from modules.access_control.domain.rbac import Principal, Role
from modules.telephony.application.sipuni import SaveConnection
from modules.telephony.composition import sipuni_service
from modules.telephony.domain.sipuni import CHANNELS, LANGUAGES, SipuniConnection
from modules.telephony.infrastructure.sipuni import UNREADABLE_SECRET
from platform_core.application.context import tenant_scope
from platform_core.infrastructure.tenancy.services import connection_registry, tenant_resolver
from platform_core.presentation.tenancy import TenantScopedAPIView
from shared.domain.errors import DomainValidationError, NotFoundError

_TAGS = ["Telephony"]


class SipuniSettingsSerializer(serializers.Serializer[dict[str, Any]]):
    account = serializers.RegexField(r"^\d{3,12}$", help_text="Sipuni cabinet number.")
    secret = serializers.CharField(
        required=False,
        allow_blank=True,
        write_only=True,
        max_length=200,
        help_text="Integration key (Settings → API integration). Empty keeps the stored key.",
    )
    company_number = serializers.CharField(max_length=20)
    extensions = serializers.DictField(
        child=serializers.UUIDField(),
        help_text='Internal number -> CRM user id, e.g. {"101": "<uuid>"}.',
    )
    country_code = serializers.RegexField(r"^\d{1,4}$", default="996")
    call_language = serializers.ChoiceField(choices=list(LANGUAGES), default="ru")
    record_channels = serializers.ChoiceField(choices=list(CHANNELS), default="mono")
    analyze_calls = serializers.BooleanField(default=True)
    enabled = serializers.BooleanField(default=True)
    rotate_token = serializers.BooleanField(default=False, write_only=True)


def _events_url(request: Request, tenant_slug: str, token: str) -> str:
    return request.build_absolute_uri(f"/api/v1/telephony/sipuni/events/{tenant_slug}/{token}/")


def _settings_to_dict(c: SipuniConnection) -> dict[str, Any]:
    return {
        "provider": "sipuni",
        "account": c.account,
        "has_secret": bool(c.secret) and c.secret != UNREADABLE_SECRET,
        "company_number": c.company_number,
        "extensions": {k: str(v) for k, v in c.extensions.items()},
        "country_code": c.country_code,
        "call_language": c.call_language,
        "record_channels": c.record_channels,
        "analyze_calls": c.analyze_calls,
        "enabled": c.enabled,
        "updated_at": c.updated_at.isoformat(),
    }


class _Scoped(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
            department_id=self.membership.department_id,
        )


class SipuniSettingsView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="sipuni_settings_get",
        summary="The company's Sipuni connection",
        description="404 when Sipuni is not connected. The integration key is never returned.",
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request) -> Response:
        connection = sipuni_service().connection(self.principal())
        if connection is None:
            raise NotFoundError("Sipuni is not connected.")
        return Response(_settings_to_dict(connection))

    @extend_schema(
        tags=_TAGS,
        operation_id="sipuni_settings_save",
        summary="Connect Sipuni or change the connection",
        description=(
            "The first save (and `rotate_token`) returns `events_url`: paste it in Sipuni "
            "(Settings → API → Events on the PBX). It holds a secret token and is shown only "
            "then. Requires integrations.manage (director)."
        ),
        request=SipuniSettingsSerializer,
        responses=OpenApiTypes.OBJECT,
    )
    def put(self, request: Request) -> Response:
        serializer = SipuniSettingsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        saved = sipuni_service().save(
            self.principal(),
            SaveConnection(
                account=data["account"],
                secret=data.get("secret") or None,
                company_number=data["company_number"],
                extensions={k: UUID(str(v)) for k, v in data["extensions"].items()},
                country_code=data["country_code"],
                call_language=data["call_language"],
                record_channels=data["record_channels"],
                analyze_calls=data["analyze_calls"],
                enabled=data["enabled"],
                rotate_token=data["rotate_token"],
            ),
        )
        body = _settings_to_dict(saved.connection)
        if saved.webhook_token:
            body["events_url"] = _events_url(request, self.tenant.slug, saved.webhook_token)
        return Response(body)

    @extend_schema(
        tags=_TAGS,
        operation_id="sipuni_settings_delete",
        summary="Disconnect Sipuni",
        responses={204: None},
    )
    def delete(self, request: Request) -> Response:
        sipuni_service().disconnect(self.principal())
        return Response(status=status.HTTP_204_NO_CONTENT)


class SipuniEventsView(APIView):
    """Called by Sipuni, not by users: no JWT. The company's slug and the secret token in
    the path select and authenticate the connection (Sipuni does not sign its events)."""

    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "webhooks"
    parser_classes = [FormParser, MultiPartParser, JSONParser]

    def finalize_response(
        self, request: Request, response: Response, *args: Any, **kwargs: Any
    ) -> Response:
        # The path holds the token: keep it out of Django's request log.
        response._has_been_logged = True  # type: ignore[attr-defined]
        return super().finalize_response(request, response, *args, **kwargs)

    def _handle(self, request: Request, tenant_slug: str, token: str, params: Any) -> Response:
        try:
            context = tenant_resolver().resolve_for_system(tenant_slug).context
        except NotFoundError:
            # An unknown company looks the same as a wrong token.
            return Response({"success": False}, status=status.HTTP_404_NOT_FOUND)
        try:
            with tenant_scope(context):
                sipuni_service().ingest(token, {k: str(v) for k, v in params.items()})
        except NotFoundError:
            return Response({"success": False}, status=status.HTTP_404_NOT_FOUND)
        except DomainValidationError:
            # A malformed event is acknowledged: Sipuni pauses deliveries after failures.
            return Response({"success": True, "ignored": True})
        finally:
            connection_registry().release(context.db_alias)
        return Response({"success": True})

    @extend_schema(
        tags=_TAGS, operation_id="sipuni_events_get", auth=[], responses=OpenApiTypes.OBJECT
    )
    def get(self, request: Request, tenant_slug: str, token: str) -> Response:
        return self._handle(request, tenant_slug, token, request.query_params)

    @extend_schema(
        tags=_TAGS,
        operation_id="sipuni_events_post",
        summary="Receive a Sipuni call event",
        description=(
            'Sipuni\'s "HTTP events": call, hang-up, answer. A hang-up of a call with a '
            "customer is logged; answered calls with a recording go to the AI. Always "
            'answers {"success": true} for a valid address, 404 otherwise.'
        ),
        auth=[],
        request=OpenApiTypes.OBJECT,
        responses=OpenApiTypes.OBJECT,
    )
    def post(self, request: Request, tenant_slug: str, token: str) -> Response:
        params = request.data if hasattr(request.data, "items") else {}
        return self._handle(request, tenant_slug, token, params or request.query_params)
