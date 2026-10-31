from __future__ import annotations

from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.organizations.composition import company_settings
from modules.sales_pipeline.application.custom_field_service import (
    CreateCustomField,
    UpdateCustomField,
)
from modules.sales_pipeline.application.deal_service import EditDeal, OpenDeal
from modules.sales_pipeline.application.loss_reason_service import UpdateLossReason
from modules.sales_pipeline.application.pipeline_service import CreatePipeline
from modules.sales_pipeline.application.ports import DealFilter
from modules.sales_pipeline.composition import (
    custom_field_service,
    deal_service,
    loss_reason_service,
    pipeline_service,
)
from modules.sales_pipeline.domain.custom_field import CustomFieldType
from modules.sales_pipeline.domain.deal import DealStatus
from modules.sales_pipeline.domain.pipeline import StageDefinition, StageKind
from modules.sales_pipeline.presentation.serializers import (
    CustomFieldCreateSerializer,
    CustomFieldListSerializer,
    CustomFieldReadSerializer,
    CustomFieldUpdateSerializer,
    DealCreateSerializer,
    DealListSerializer,
    DealLoseSerializer,
    DealMoveSerializer,
    DealReadSerializer,
    DealUpdateSerializer,
    LossReasonCreateSerializer,
    LossReasonListSerializer,
    LossReasonReadSerializer,
    LossReasonUpdateSerializer,
    PipelineCreateSerializer,
    PipelineReadSerializer,
    StageChangeListSerializer,
    custom_field_to_dict,
    deal_to_dict,
    loss_reason_to_dict,
    pipeline_to_dict,
    stage_change_to_dict,
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
_SEARCH = OpenApiParameter(
    "search", str, OpenApiParameter.QUERY, description="Case-insensitive match on title or source."
)
_MANAGER = OpenApiParameter(
    "manager_id", str, OpenApiParameter.QUERY, description="Filter by the deal owner (manager)."
)
_CUSTOMER = OpenApiParameter(
    "customer_id", str, OpenApiParameter.QUERY, description="Filter by linked customer."
)
_CREATED_FROM = OpenApiParameter(
    "created_from",
    str,
    OpenApiParameter.QUERY,
    description="Created on/after this date (YYYY-MM-DD).",
)
_CREATED_TO = OpenApiParameter(
    "created_to",
    str,
    OpenApiParameter.QUERY,
    description="Created on/before this date (YYYY-MM-DD).",
)
_ATTENTION = OpenApiParameter(
    "attention",
    str,
    OpenApiParameter.QUERY,
    description="Needs attention: 'overdue' (past expected close, open) or 'no_next_step'.",
    enum=["overdue", "no_next_step"],
)
_ORDERING = OpenApiParameter(
    "ordering",
    str,
    OpenApiParameter.QUERY,
    description="Sort field, '-' for descending: created_at, updated_at, amount, title.",
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
            department_id=self.membership.department_id,
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
                    StageDefinition(
                        name=s["name"],
                        probability=s["probability"],
                        kind=StageKind(s["kind"]),
                    )
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
        parameters=[
            _TENANT_HEADER,
            _STATUS,
            _PIPELINE,
            _STAGE,
            _SEARCH,
            _MANAGER,
            _CUSTOMER,
            _CREATED_FROM,
            _CREATED_TO,
            _ATTENTION,
            _ORDERING,
            _LIMIT,
            _OFFSET,
        ],
        responses=DealListSerializer,
    )
    def get(self, request: Request) -> Response:
        try:
            limit = min(max(int(request.query_params.get("limit", 50)), 1), MAX_PAGE_SIZE)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except ValueError:
            limit, offset = 50, 0
        q = request.query_params
        status_value = q.get("status")
        valid = {s.value for s in DealStatus}
        attention = q.get("attention")
        attention = attention if attention in {"overdue", "no_next_step"} else None
        search = (q.get("search") or "").strip() or None
        filters = DealFilter(
            status=DealStatus(status_value) if status_value in valid else None,
            pipeline_id=_as_uuid(q.get("pipeline_id")),
            stage_id=_as_uuid(q.get("stage_id")),
            search=search,
            owner_user_id=_as_uuid(q.get("manager_id")),
            customer_id=_as_uuid(q.get("customer_id")),
            created_from=_as_date(q.get("created_from")),
            created_to=_as_date(q.get("created_to")),
            attention=attention,
            today=self._company_today(),
            ordering=q.get("ordering"),
        )
        page = deal_service().list_deals(self.principal(), filters, limit=limit, offset=offset)
        return Response({"count": page.total, "results": [deal_to_dict(d) for d in page.items]})

    def _company_today(self) -> date:
        tz = ZoneInfo(str(company_settings(self.tenant.tenant_id)["timezone"]))
        return datetime.now(tz).date()

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
                currency=data.get("currency")
                or company_settings(self.tenant.tenant_id)["currency"],
                pipeline_id=data.get("pipeline_id"),
                customer_id=data.get("customer_id"),
                source=data.get("source"),
                next_step=data.get("next_step"),
                custom_fields=data.get("custom_fields"),
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
                source=data.get("source"),
                set_source="source" in request.data,
                next_step=data.get("next_step"),
                set_next_step="next_step" in request.data,
                custom_fields=data.get("custom_fields"),
                set_custom_fields="custom_fields" in request.data,
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
        description=(
            "Transitions an open deal to a stage of its own pipeline. Moving into a "
            "`won`-kind stage closes the deal as won; a `lost`-kind stage closes it as lost "
            "and requires `reason`. An `open`-kind stage just moves the deal."
        ),
        parameters=[_TENANT_HEADER],
        request=DealMoveSerializer,
        responses=DealReadSerializer,
    )
    def post(self, request: Request, deal_id: UUID) -> Response:
        serializer = DealMoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        deal = deal_service().move_stage(
            self.principal(),
            deal_id,
            serializer.validated_data["stage_id"],
            reason=serializer.validated_data.get("reason"),
            loss_reason_id=serializer.validated_data.get("loss_reason_id"),
        )
        return Response(deal_to_dict(deal))


class DealStageHistoryView(_Scoped):
    @extend_schema(
        tags=_DEAL_TAGS,
        operation_id="deals_stage_history",
        summary="Deal stage history",
        description=(
            "Append-only history of a deal's stage transitions (move, win, lost, reopen), "
            "oldest first: where it went, who moved it, the deal version and, for a loss, "
            "the reason. History starts at the P0-2 migration."
        ),
        parameters=[_TENANT_HEADER],
        responses=StageChangeListSerializer,
    )
    def get(self, request: Request, deal_id: UUID) -> Response:
        changes = deal_service().stage_history(self.principal(), deal_id)
        return Response(
            {"count": len(changes), "results": [stage_change_to_dict(c) for c in changes]}
        )


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


# --- loss reasons -----------------------------------------------------------


class LossReasonListCreateView(_Scoped):
    @extend_schema(
        tags=_PIPELINE_TAGS,
        operation_id="loss_reasons_list",
        summary="List loss reasons",
        description=(
            "The company's loss-reason catalog. Pass `active=true` to list only active "
            "reasons (the ones selectable when losing a deal)."
        ),
        parameters=[_TENANT_HEADER],
        responses=LossReasonListSerializer,
    )
    def get(self, request: Request) -> Response:
        active_only = request.query_params.get("active") in {"1", "true", "True"}
        reasons = loss_reason_service().list_reasons(self.principal(), active_only=active_only)
        return Response(
            {"count": len(reasons), "results": [loss_reason_to_dict(r) for r in reasons]}
        )

    @extend_schema(
        tags=_PIPELINE_TAGS,
        operation_id="loss_reasons_create",
        summary="Add a loss reason",
        description="Adds a loss reason to the catalog. Requires the pipelines.manage permission.",
        parameters=[_TENANT_HEADER],
        request=LossReasonCreateSerializer,
        responses={201: LossReasonReadSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = LossReasonCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = loss_reason_service().create(self.principal(), serializer.validated_data["name"])
        return Response(loss_reason_to_dict(reason), status=status.HTTP_201_CREATED)


class LossReasonDetailView(_Scoped):
    @extend_schema(
        tags=_PIPELINE_TAGS,
        operation_id="loss_reasons_update",
        summary="Rename or (de)activate a loss reason",
        description=(
            "Renames a loss reason or toggles `is_active`. Reasons are deactivated, never "
            "deleted, so historical deals keep their reason. Requires pipelines.manage."
        ),
        parameters=[_TENANT_HEADER],
        request=LossReasonUpdateSerializer,
        responses=LossReasonReadSerializer,
    )
    def patch(self, request: Request, loss_reason_id: UUID) -> Response:
        serializer = LossReasonUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        reason = loss_reason_service().update(
            self.principal(),
            UpdateLossReason(
                reason_id=loss_reason_id,
                name=data.get("name"),
                is_active=data.get("is_active"),
            ),
        )
        return Response(loss_reason_to_dict(reason))


# --- custom fields ----------------------------------------------------------


class CustomFieldListCreateView(_Scoped):
    @extend_schema(
        tags=_PIPELINE_TAGS,
        operation_id="custom_fields_list",
        summary="List custom field definitions",
        description=(
            "The company's custom deal-field definitions. Pass `active=true` for only the "
            "active ones (those validated when a deal is created or edited)."
        ),
        parameters=[_TENANT_HEADER],
        responses=CustomFieldListSerializer,
    )
    def get(self, request: Request) -> Response:
        active_only = request.query_params.get("active") in {"1", "true", "True"}
        fields = custom_field_service().list_fields(self.principal(), active_only=active_only)
        return Response(
            {"count": len(fields), "results": [custom_field_to_dict(f) for f in fields]}
        )

    @extend_schema(
        tags=_PIPELINE_TAGS,
        operation_id="custom_fields_create",
        summary="Define a custom field",
        description=(
            "Defines a typed custom field on deals. `field_type` is one of text, number, "
            "date, datetime, boolean, single_select, multi_select, money, url; select types "
            "need `options`. Requires the pipelines.manage permission."
        ),
        parameters=[_TENANT_HEADER],
        request=CustomFieldCreateSerializer,
        responses={201: CustomFieldReadSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = CustomFieldCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        definition = custom_field_service().create(
            self.principal(),
            CreateCustomField(
                key=data["key"],
                label=data["label"],
                field_type=CustomFieldType(data["field_type"]),
                options=data.get("options", []),
                required=data["required"],
            ),
        )
        return Response(custom_field_to_dict(definition), status=status.HTTP_201_CREATED)


class CustomFieldDetailView(_Scoped):
    @extend_schema(
        tags=_PIPELINE_TAGS,
        operation_id="custom_fields_update",
        summary="Update a custom field",
        description=(
            "Relabels a custom field, changes its options, toggles required or deactivates "
            "it. Fields are deactivated, never deleted. Requires pipelines.manage."
        ),
        parameters=[_TENANT_HEADER],
        request=CustomFieldUpdateSerializer,
        responses=CustomFieldReadSerializer,
    )
    def patch(self, request: Request, field_id: UUID) -> Response:
        serializer = CustomFieldUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        definition = custom_field_service().update(
            self.principal(),
            UpdateCustomField(
                definition_id=field_id,
                label=data.get("label"),
                options=data.get("options"),
                required=data.get("required"),
                is_active=data.get("is_active"),
            ),
        )
        return Response(custom_field_to_dict(definition))


def _as_uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _as_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
