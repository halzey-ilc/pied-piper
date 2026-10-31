from __future__ import annotations

from uuid import UUID

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.leads.application.service import CaptureLead, UpdateLead
from modules.leads.composition import lead_service
from modules.leads.domain.lead import LeadSource, LeadStatus
from modules.leads.presentation.serializers import (
    ConversionResultSerializer,
    DuplicatesListSerializer,
    LeadConvertSerializer,
    LeadCreateSerializer,
    LeadDisqualifySerializer,
    LeadListSerializer,
    LeadReadSerializer,
    LeadUpdateSerializer,
    duplicate_to_dict,
    lead_to_dict,
)
from modules.organizations.composition import company_settings
from modules.sales_pipeline.application.deal_service import OpenDeal
from modules.sales_pipeline.composition import deal_service
from platform_core.presentation.tenancy import TenantScopedAPIView

_TENANT_HEADER = OpenApiParameter(
    "X-Tenant",
    str,
    OpenApiParameter.HEADER,
    required=True,
    description="Short name (slug) of your company, e.g. `demo`. Get it from GET /api/v1/me/.",
    examples=[OpenApiExample("Demo tenant", value="demo")],
)
_STATUS = OpenApiParameter(
    "status",
    str,
    OpenApiParameter.QUERY,
    description="Filter by status.",
    enum=[s.value for s in LeadStatus],
)
_LIMIT = OpenApiParameter(
    "limit", int, OpenApiParameter.QUERY, description="Page size 1-200 (default 50)."
)
_OFFSET = OpenApiParameter(
    "offset", int, OpenApiParameter.QUERY, description="Rows to skip (default 0)."
)

MAX_PAGE_SIZE = 200
_TAGS = ["Leads"]


class LeadBaseView(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
            department_id=self.membership.department_id,
        )


class LeadListCreateView(LeadBaseView):
    @extend_schema(
        tags=_TAGS,
        operation_id="leads_list",
        summary="List leads",
        description=(
            "Returns leads visible to you in the selected company, newest first. "
            "Filter by `status` and page with `limit`/`offset`. Visibility follows your "
            "role (own / team / all)."
        ),
        parameters=[_TENANT_HEADER, _STATUS, _LIMIT, _OFFSET],
        responses=LeadListSerializer,
    )
    def get(self, request: Request) -> Response:
        try:
            limit = min(max(int(request.query_params.get("limit", 50)), 1), MAX_PAGE_SIZE)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except ValueError:
            limit, offset = 50, 0
        status_value = request.query_params.get("status")
        valid = {s.value for s in LeadStatus}
        lead_status = LeadStatus(status_value) if status_value in valid else None
        page = lead_service().list_leads(
            self.principal(), status=lead_status, limit=limit, offset=offset
        )
        return Response(
            {"count": page.total, "results": [lead_to_dict(lead) for lead in page.items]}
        )

    @extend_schema(
        tags=_TAGS,
        operation_id="leads_create",
        summary="Capture a lead",
        description=(
            "Creates a new lead; you become its owner. `name` is required and at least one "
            "of `email` or `phone` must be given (phone in E.164, e.g. `+77011234567`)."
        ),
        parameters=[_TENANT_HEADER],
        request=LeadCreateSerializer,
        responses={201: LeadReadSerializer},
        examples=[
            OpenApiExample(
                "New lead",
                value={"name": "Jane Buyer", "source": "website", "email": "jane@example.test"},
                request_only=True,
            )
        ],
    )
    def post(self, request: Request) -> Response:
        serializer = LeadCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        lead = lead_service().capture(
            self.principal(),
            CaptureLead(
                name=data["name"],
                source=LeadSource(data["source"]),
                email=data.get("email") or None,
                phone=data.get("phone") or None,
            ),
        )
        return Response(lead_to_dict(lead), status=status.HTTP_201_CREATED)


class LeadDetailView(LeadBaseView):
    @extend_schema(
        tags=_TAGS,
        operation_id="leads_retrieve",
        summary="Get one lead",
        description="Returns a lead by id, or 404 if it does not exist or is not visible to you.",
        parameters=[_TENANT_HEADER],
        responses=LeadReadSerializer,
    )
    def get(self, request: Request, lead_id: UUID) -> Response:
        return Response(lead_to_dict(lead_service().get(self.principal(), lead_id)))

    @extend_schema(
        tags=_TAGS,
        operation_id="leads_update",
        summary="Update a lead",
        description=(
            "Updates name, email or phone. Send only changed fields. Optional `version` "
            "gives optimistic locking (409 on mismatch). A converted lead cannot be edited."
        ),
        parameters=[_TENANT_HEADER],
        request=LeadUpdateSerializer,
        responses=LeadReadSerializer,
    )
    def patch(self, request: Request, lead_id: UUID) -> Response:
        serializer = LeadUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        lead = lead_service().update(
            self.principal(),
            UpdateLead(
                lead_id=lead_id,
                expected_version=data.get("version"),
                name=data.get("name"),
                email=data.get("email"),
                phone=data.get("phone"),
            ),
        )
        return Response(lead_to_dict(lead))


class LeadQualifyView(LeadBaseView):
    @extend_schema(
        tags=_TAGS,
        operation_id="leads_qualify",
        summary="Qualify a lead",
        description="Marks a new or previously unqualified lead as qualified.",
        parameters=[_TENANT_HEADER],
        request=None,
        responses=LeadReadSerializer,
    )
    def post(self, request: Request, lead_id: UUID) -> Response:
        return Response(lead_to_dict(lead_service().qualify(self.principal(), lead_id)))


class LeadDisqualifyView(LeadBaseView):
    @extend_schema(
        tags=_TAGS,
        operation_id="leads_disqualify",
        summary="Disqualify a lead",
        description=(
            "Marks a lead as unqualified with a required reason. "
            "A converted lead cannot be disqualified."
        ),
        parameters=[_TENANT_HEADER],
        request=LeadDisqualifySerializer,
        responses=LeadReadSerializer,
    )
    def post(self, request: Request, lead_id: UUID) -> Response:
        serializer = LeadDisqualifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        lead = lead_service().disqualify(
            self.principal(), lead_id, serializer.validated_data["reason"]
        )
        return Response(lead_to_dict(lead))


class LeadConvertView(LeadBaseView):
    @extend_schema(
        tags=_TAGS,
        operation_id="leads_convert",
        summary="Convert a lead to a customer",
        description=(
            "Converts a qualified lead into a customer and returns the new customer id. "
            "Idempotent: converting an already-converted lead returns the existing customer "
            "without creating a duplicate. Requires the leads.convert and customers.create "
            "permissions."
        ),
        parameters=[_TENANT_HEADER],
        request=LeadConvertSerializer,
        responses=ConversionResultSerializer,
    )
    def post(self, request: Request, lead_id: UUID) -> Response:
        serializer = LeadConvertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        create_deal = serializer.validated_data.get("create_deal")
        principal = self.principal()
        result = lead_service().convert(principal, lead_id)
        deal_id: str | None = None
        if create_deal is not None:
            currency = (
                create_deal.get("currency") or company_settings(self.tenant.tenant_id)["currency"]
            )
            deal = deal_service().open_deal(
                principal,
                OpenDeal(
                    title=create_deal["title"],
                    amount=create_deal["amount"],
                    currency=currency,
                    pipeline_id=create_deal.get("pipeline_id"),
                    customer_id=result.customer_id,
                ),
            )
            deal_id = str(deal.id)
        return Response(
            {
                "lead": lead_to_dict(result.lead),
                "customer_id": str(result.customer_id),
                "already_converted": result.already_converted,
                "deal_id": deal_id,
            }
        )


class LeadDuplicatesView(LeadBaseView):
    @extend_schema(
        tags=_TAGS,
        operation_id="leads_duplicates",
        summary="Find duplicate candidates for a lead",
        description=(
            "Returns leads and existing customers in the company that share this lead's "
            "e-mail or phone (matched by digits). No automatic merge: the caller decides. "
            "Requires leads.view."
        ),
        parameters=[_TENANT_HEADER],
        responses=DuplicatesListSerializer,
    )
    def get(self, request: Request, lead_id: UUID) -> Response:
        candidates = lead_service().find_duplicates(self.principal(), lead_id)
        return Response(
            {"count": len(candidates), "results": [duplicate_to_dict(c) for c in candidates]}
        )
