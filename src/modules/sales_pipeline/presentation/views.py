from __future__ import annotations

from uuid import UUID

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.sales_pipeline.application.deal_service import EditDeal, OpenDeal
from modules.sales_pipeline.application.pipeline_service import CreatePipeline
from modules.sales_pipeline.application.ports import DealFilter
from modules.sales_pipeline.composition import deal_service, pipeline_service
from modules.sales_pipeline.domain.deal import DealStatus
from modules.sales_pipeline.domain.pipeline import StageDefinition
from modules.sales_pipeline.presentation.serializers import (
    DealCreateSerializer,
    DealListSerializer,
    DealLoseSerializer,
    DealMoveSerializer,
    DealReadSerializer,
    DealUpdateSerializer,
    PipelineCreateSerializer,
    PipelineReadSerializer,
    deal_to_dict,
    pipeline_to_dict,
)
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
    description="Filter deals by status.",
    enum=[s.value for s in DealStatus],
)
_PIPELINE = OpenApiParameter(
    "pipeline_id", str, OpenApiParameter.QUERY, description="Filter by pipeline."
)
_STAGE = OpenApiParameter("stage_id", str, OpenApiParameter.QUERY, description="Filter by stage.")
_LIMIT = OpenApiParameter(
    "limit", int, OpenApiParameter.QUERY, description="Page size 1-200 (default 50)."
)
_OFFSET = OpenApiParameter(
    "offset", int, OpenApiParameter.QUERY, description="Rows to skip (default 0)."
)

MAX_PAGE_SIZE = 200
_PIPELINE_TAGS = ["Pipelines"]
_DEAL_TAGS = ["Deals"]


class _Scoped(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
        )


# --- pipelines --------------------------------------------------------------


class PipelineListCreateView(_Scoped):
    @extend_schema(
        tags=_PIPELINE_TAGS,
        operation_id="pipelines_list",
        summary="List pipelines",
        description="Returns all pipelines in the company with their ordered stages.",
        parameters=[_TENANT_HEADER],
        responses=PipelineReadSerializer(many=True),
    )
    def get(self, request: Request) -> Response:
        pipelines = pipeline_service().list_pipelines(self.principal())
        return Response([pipeline_to_dict(p) for p in pipelines])

    @extend_schema(
        tags=_PIPELINE_TAGS,
        operation_id="pipelines_create",
        summary="Create a pipeline",
        description=(
            "Creates a pipeline with ordered stages (1-20). Stages are positioned in the "
            "order given. Setting `is_default` makes this the pipeline used when a deal is "
            "created without one. Requires the pipelines.manage permission."
        ),
        parameters=[_TENANT_HEADER],
        request=PipelineCreateSerializer,
        responses={201: PipelineReadSerializer},
        examples=[
            OpenApiExample(
                "Default pipeline",
                value={
                    "name": "Standard sales",
                    "is_default": True,
                    "stages": [
                        {"name": "New", "probability": 10},
                        {"name": "Negotiation", "probability": 50},
                        {"name": "Proposal sent", "probability": 80},
                    ],
                },
                request_only=True,
            )
        ],
    )
    def post(self, request: Request) -> Response:
        serializer = PipelineCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        pipeline = pipeline_service().create(
            self.principal(),
            CreatePipeline(
                name=data["name"],
                is_default=data["is_default"],
                stages=[
                    StageDefinition(name=s["name"], probability=s["probability"])
                    for s in data["stages"]
                ],
            ),
        )
        return Response(pipeline_to_dict(pipeline), status=status.HTTP_201_CREATED)


class PipelineDetailView(_Scoped):
    @extend_schema(
        tags=_PIPELINE_TAGS,
        operation_id="pipelines_retrieve",
        summary="Get one pipeline",
        description="Returns a pipeline and its stages by id, or 404 if it does not exist.",
        parameters=[_TENANT_HEADER],
        responses=PipelineReadSerializer,
    )
    def get(self, request: Request, pipeline_id: UUID) -> Response:
        return Response(pipeline_to_dict(pipeline_service().get(self.principal(), pipeline_id)))


# --- deals ------------------------------------------------------------------


class DealListCreateView(_Scoped):
    @extend_schema(
        tags=_DEAL_TAGS,
        operation_id="deals_list",
        summary="List deals",
        description=(
            "Returns deals visible to you (own / team / all by role), newest first. Filter "
            "by `status`, `pipeline_id`, `stage_id` and page with `limit`/`offset`."
        ),
        parameters=[_TENANT_HEADER, _STATUS, _PIPELINE, _STAGE, _LIMIT, _OFFSET],
        responses=DealListSerializer,
    )
    def get(self, request: Request) -> Response:
        try:
            limit = min(max(int(request.query_params.get("limit", 50)), 1), MAX_PAGE_SIZE)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except ValueError:
            limit, offset = 50, 0
        status_value = request.query_params.get("status")
        valid = {s.value for s in DealStatus}
        filters = DealFilter(
            status=DealStatus(status_value) if status_value in valid else None,
            pipeline_id=_as_uuid(request.query_params.get("pipeline_id")),
            stage_id=_as_uuid(request.query_params.get("stage_id")),
        )
        page = deal_service().list_deals(self.principal(), filters, limit=limit, offset=offset)
        return Response({"count": page.total, "results": [deal_to_dict(d) for d in page.items]})

    @extend_schema(
        tags=_DEAL_TAGS,
        operation_id="deals_create",
        summary="Open a deal",
        description=(
            "Opens a deal in a pipeline's first stage. If `pipeline_id` is omitted the "
            "default pipeline is used. `amount` is non-negative; `currency` is a 3-letter "
            "code. An optional `customer_id` links the deal to an existing customer."
        ),
        parameters=[_TENANT_HEADER],
        request=DealCreateSerializer,
        responses={201: DealReadSerializer},
        examples=[
            OpenApiExample(
                "New deal",
                value={"title": "Acme - 10 seats", "amount": "1200.00", "currency": "USD"},
                request_only=True,
            )
        ],
    )
    def post(self, request: Request) -> Response:
        serializer = DealCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        deal = deal_service().open_deal(
            self.principal(),
            OpenDeal(
                title=data["title"],
                amount=data["amount"],
                currency=data["currency"],
                pipeline_id=data.get("pipeline_id"),
                customer_id=data.get("customer_id"),
                expected_close_date=data.get("expected_close_date"),
            ),
        )
        return Response(deal_to_dict(deal), status=status.HTTP_201_CREATED)


class DealDetailView(_Scoped):
    @extend_schema(
        tags=_DEAL_TAGS,
        operation_id="deals_retrieve",
        summary="Get one deal",
        description="Returns a deal by id, or 404 if it does not exist or is not visible to you.",
        parameters=[_TENANT_HEADER],
        responses=DealReadSerializer,
    )
    def get(self, request: Request, deal_id: UUID) -> Response:
        return Response(deal_to_dict(deal_service().get(self.principal(), deal_id)))

    @extend_schema(
        tags=_DEAL_TAGS,
        operation_id="deals_update",
        summary="Update a deal",
        description=(
            "Edits an open deal's title, amount/currency, linked customer or expected close "
            "date. Send only changed fields; pass `customer_id`/`expected_close_date` as "
            "null to clear them. Optional `version` gives optimistic locking (409 on "
            "mismatch). Won/lost deals are read-only until reopened."
        ),
        parameters=[_TENANT_HEADER],
        request=DealUpdateSerializer,
        responses=DealReadSerializer,
    )
    def patch(self, request: Request, deal_id: UUID) -> Response:
        serializer = DealUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        deal = deal_service().edit(
            self.principal(),
            EditDeal(
                deal_id=deal_id,
                expected_version=data.get("version"),
                title=data.get("title"),
                amount=data.get("amount"),
                currency=data.get("currency"),
                customer_id=data.get("customer_id"),
                set_customer="customer_id" in request.data,
                expected_close_date=data.get("expected_close_date"),
                set_expected_close="expected_close_date" in request.data,
            ),
        )
        return Response(deal_to_dict(deal))


class DealMoveView(_Scoped):
    @extend_schema(
        tags=_DEAL_TAGS,
        operation_id="deals_move_stage",
        summary="Move a deal to another stage",
        description="Moves an open deal to a stage of its own pipeline.",
        parameters=[_TENANT_HEADER],
        request=DealMoveSerializer,
        responses=DealReadSerializer,
    )
    def post(self, request: Request, deal_id: UUID) -> Response:
        serializer = DealMoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        deal = deal_service().move_stage(
            self.principal(), deal_id, serializer.validated_data["stage_id"]
        )
        return Response(deal_to_dict(deal))


class DealWinView(_Scoped):
    @extend_schema(
        tags=_DEAL_TAGS,
        operation_id="deals_win",
        summary="Mark a deal won",
        description="Closes an open deal as won and records the close time.",
        parameters=[_TENANT_HEADER],
        request=None,
        responses=DealReadSerializer,
    )
    def post(self, request: Request, deal_id: UUID) -> Response:
        return Response(deal_to_dict(deal_service().win(self.principal(), deal_id)))


class DealLoseView(_Scoped):
    @extend_schema(
        tags=_DEAL_TAGS,
        operation_id="deals_lose",
        summary="Mark a deal lost",
        description="Closes an open deal as lost with a required reason.",
        parameters=[_TENANT_HEADER],
        request=DealLoseSerializer,
        responses=DealReadSerializer,
    )
    def post(self, request: Request, deal_id: UUID) -> Response:
        serializer = DealLoseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        deal = deal_service().lose(self.principal(), deal_id, serializer.validated_data["reason"])
        return Response(deal_to_dict(deal))


class DealReopenView(_Scoped):
    @extend_schema(
        tags=_DEAL_TAGS,
        operation_id="deals_reopen",
        summary="Reopen a closed deal",
        description="Reopens a won or lost deal back into its last stage.",
        parameters=[_TENANT_HEADER],
        request=None,
        responses=DealReadSerializer,
    )
    def post(self, request: Request, deal_id: UUID) -> Response:
        return Response(deal_to_dict(deal_service().reopen(self.principal(), deal_id)))


def _as_uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None
