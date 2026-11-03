"""Website lead forms API.

Management (tenant-scoped, JWT + X-Tenant; director: any department, head of sales: own):
    GET/POST /api/v1/web-forms/keys/
    POST     /api/v1/web-forms/keys/{id}/revoke/

Public (no authentication; the token selects the company and department):
    POST /api/v1/public/leads/{token}/
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.access_control.domain.rbac import Principal, Role
from modules.web_forms.application.service import CreateKey, UnknownKeyError, resolve_key
from modules.web_forms.composition import key_store, web_form_key_service, website_lead_intake
from modules.web_forms.domain.key import WebsiteSubmission
from modules.web_forms.infrastructure.store import department_names, tenant_slug
from modules.web_forms.presentation.serializers import (
    HONEYPOT_FIELD,
    KeyCreateSerializer,
    KeyListSerializer,
    KeyReadSerializer,
    PublicLeadAcceptedSerializer,
    PublicLeadSerializer,
    creator_names,
    key_to_dict,
)
from modules.web_forms.presentation.throttles import WebFormIpThrottle, WebFormTokenThrottle
from platform_core.application.context import tenant_scope
from platform_core.domain.tenancy import TenantNotFoundError
from platform_core.infrastructure.tenancy.services import (
    connection_registry,
    tenant_access_gate,
    tenant_resolver,
)
from platform_core.presentation.errors import error_body
from platform_core.presentation.tenancy import TenantScopedAPIView
from shared.domain.errors import DomainValidationError

MAX_PUBLIC_BODY_BYTES = 16 * 1024

_TENANT_HEADER = OpenApiParameter(
    "X-Tenant",
    str,
    OpenApiParameter.HEADER,
    required=True,
    description="Short name (slug) of your company, e.g. `demo`.",
    examples=[OpenApiExample("Demo tenant", value="demo")],
)
_DEPARTMENT = OpenApiParameter(
    "department_id",
    str,
    OpenApiParameter.QUERY,
    description="Department (director: optional filter; head of sales: own, may omit).",
)
_TAGS = ["Website forms"]


class _Scoped(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
            department_id=self.membership.department_id,
        )


def _department(request: Request) -> UUID | None:
    raw = request.query_params.get("department_id")
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError as exc:
        raise DomainValidationError("department_id must be a UUID.") from exc


class WebFormKeyListCreateView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="web_form_keys_list",
        summary="List website form keys",
        description=(
            "Keys of the company (director; optionally one department) or of your own "
            "department (head of sales), newest first, revoked ones included. Others: 403."
        ),
        parameters=[_TENANT_HEADER, _DEPARTMENT],
        responses=KeyListSerializer,
    )
    def get(self, request: Request) -> Response:
        keys = web_form_key_service().list_keys(self.principal(), _department(request))
        tenant_id = self.tenant.tenant_id
        departments = department_names(tenant_id)
        names = creator_names(tenant_id, keys)
        return Response(
            {"count": len(keys), "results": [key_to_dict(k, departments, names) for k in keys]}
        )

    @extend_schema(
        tags=_TAGS,
        operation_id="web_form_keys_create",
        summary="Create a website form key",
        description=(
            "Creates a key for a department: the director names `department_id`, the head of "
            "sales gets their own department. Leads sent with the key are routed like leads "
            "the head of sales captures. Up to 20 active keys per department (409)."
        ),
        parameters=[_TENANT_HEADER],
        request=KeyCreateSerializer,
        responses={201: KeyReadSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = KeyCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        key = web_form_key_service().create(
            self.principal(), CreateKey(name=data["name"], department_id=data["department_id"])
        )
        tenant_id = self.tenant.tenant_id
        body = key_to_dict(key, department_names(tenant_id), creator_names(tenant_id, [key]))
        return Response(body, status=status.HTTP_201_CREATED)


class WebFormKeyRevokeView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="web_form_keys_revoke",
        summary="Revoke a website form key",
        description=(
            "The website form stops working at once (its endpoint answers 404). Idempotent. "
            "A head of sales cannot see or revoke other departments' keys (404)."
        ),
        parameters=[_TENANT_HEADER],
        request=None,
        responses=KeyReadSerializer,
    )
    def post(self, request: Request, key_id: UUID) -> Response:
        key = web_form_key_service().revoke(self.principal(), key_id)
        tenant_id = self.tenant.tenant_id
        return Response(
            key_to_dict(key, department_names(tenant_id), creator_names(tenant_id, [key]))
        )


def _accepted() -> Response:
    return Response({"status": "accepted"}, status=status.HTTP_202_ACCEPTED)


class PublicLeadView(APIView):
    """Called from a company's website: no JWT, no X-Tenant. CORS is open for this path
    only (see web_forms.infrastructure.cors)."""

    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]
    throttle_classes = [WebFormIpThrottle, WebFormTokenThrottle]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def finalize_response(
        self, request: Request, response: Response, *args: Any, **kwargs: Any
    ) -> Response:
        # The path holds the key's token: keep it out of Django's request log (4xx/5xx
        # warnings print the raw path). The access log records the route template only.
        response._has_been_logged = True  # type: ignore[attr-defined]
        return super().finalize_response(request, response, *args, **kwargs)

    @extend_schema(
        tags=_TAGS,
        operation_id="public_leads_submit",
        summary="Send a lead from a website form",
        description=(
            "Public endpoint for website forms. `name` is required and at least one of "
            "`phone` (international, e.g. `+996555123456`) or `email`. Optional `comment`, "
            "`page_url`, `utm_source`, `utm_medium`, `utm_campaign` are attached to the lead "
            "as a note. `website` is a honeypot: keep it empty and hidden. The lead gets "
            "source `website` and is routed like a lead captured by the head of sales. "
            "202 on success (also for a repeated submission of the same contact within 10 "
            "minutes, which creates nothing new); 404 for an unknown or revoked key; 403 if "
            "the company's subscription is inactive; 400 invalid data; 429 too many requests."
        ),
        auth=[],
        request=PublicLeadSerializer,
        responses={202: PublicLeadAcceptedSerializer},
    )
    def post(self, request: Request, token: str) -> Response:
        if len(request.body) > MAX_PUBLIC_BODY_BYTES:
            return Response(
                error_body("payload_too_large", "Payload too large."),
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        key = resolve_key(key_store(), token)
        slug = tenant_slug(key.tenant_id)
        if slug is None:
            raise UnknownKeyError
        try:
            context = tenant_resolver().resolve_for_system(slug).context
        except TenantNotFoundError:
            raise UnknownKeyError from None  # suspended or removed company
        try:
            gate = tenant_access_gate()
            if gate is not None:  # inactive subscription: refused like any company request
                gate.check(
                    context.tenant_id, request.path, method="POST", db_alias=context.db_alias
                )
            if not isinstance(request.data, Mapping):
                raise DomainValidationError("Send a JSON object or form fields.")
            if request.data.get(HONEYPOT_FIELD):
                return _accepted()  # a bot: pretend success, create nothing
            serializer = PublicLeadSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data
            submission = WebsiteSubmission(
                name=data["name"],
                phone=(data.get("phone") or "").strip() or None,
                email=(data.get("email") or "").strip() or None,
                comment=data.get("comment") or None,
                page_url=data.get("page_url") or None,
                utm_source=data.get("utm_source") or None,
                utm_medium=data.get("utm_medium") or None,
                utm_campaign=data.get("utm_campaign") or None,
            )
            with tenant_scope(context):
                website_lead_intake().submit(key, submission)
        finally:
            connection_registry().release(context.db_alias)
        return _accepted()
