from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.activities.presentation.serializers import next_step_from
from modules.customers.composition import customer_service
from modules.organizations.composition import (
    company_settings,
    member_display_name,
    people_in_details,
)
from modules.sales_pipeline.application.custom_field_service import (
    CreateCustomField,
    UpdateCustomField,
)
from modules.sales_pipeline.application.deal_service import EditDeal, OpenDeal, StageMoveExtras
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
from modules.sales_pipeline.infrastructure.next_tasks import next_tasks
from modules.sales_pipeline.presentation.serializers import (
    AdoptDealsSerializer,
    AdoptionPreviewSerializer,
    AdoptionResultSerializer,
    CustomFieldCreateSerializer,
    CustomFieldListSerializer,
    CustomFieldReadSerializer,
    CustomFieldUpdateSerializer,
    DealAssignSerializer,
    DealCreateSerializer,
    DealDetailSerializer,
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
    PipelineUpdateSerializer,
    StageAddSerializer,
    StageChangeListSerializer,
    StageOrderSerializer,
    StageRemovalSerializer,
    StageUpdateSerializer,
    custom_field_to_dict,
    deal_to_dict,
    loss_reason_to_dict,
    pipeline_to_dict,
    stage_change_to_dict,
)
from platform_core.presentation.tenancy import TenantScopedAPIView
from shared.domain.errors import DomainValidationError, NotFoundError

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
_CLOSED_FROM = OpenApiParameter(
    "closed_from",
    str,
    OpenApiParameter.QUERY,
    description="Closed (won or lost) on/after this date (YYYY-MM-DD).",
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
        description=(
            "Pipelines you can see, with their ordered stages: the company-wide ones and, for "
            "a department member, their department's own (`department_id`), which come first "
            "— default first. The director sees every department's pipelines."
        ),
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
            "created without one (within its department, or company-wide). A head of sales "
            "creates their department's pipeline (`department_id` is set for them; a "
            "department's first pipeline becomes its default); the director may pass any "
            "`department_id` or none. Requires the pipelines.manage permission."
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
                department_id=data.get("department_id"),
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
        description=(
            "Returns a pipeline and its stages by id, or 404 if it does not exist or belongs "
            "to another department. `counts=true` (for whoever may configure it) adds "
            "`open_deals` and `total_deals` per stage, across all owners."
        ),
        parameters=[
            _TENANT_HEADER,
            OpenApiParameter("counts", bool, OpenApiParameter.QUERY, description="Add deal counts"),
        ],
        responses=PipelineReadSerializer,
    )
    def get(self, request: Request, pipeline_id: UUID) -> Response:
        service = pipeline_service()
        pipeline = service.get(self.principal(), pipeline_id)
        counts = (
            service.stage_counts(self.principal(), pipeline_id)
            if request.query_params.get("counts") == "true"
            else None
        )
        return Response(pipeline_to_dict(pipeline, counts))

    @extend_schema(
        tags=_PIPELINE_TAGS,
        operation_id="pipelines_update",
        summary="Rename a pipeline or make it the default",
        description="Requires pipelines.manage. `is_default: true` moves the default flag here.",
        parameters=[_TENANT_HEADER],
        request=PipelineUpdateSerializer,
        responses=PipelineReadSerializer,
    )
    def patch(self, request: Request, pipeline_id: UUID) -> Response:
        serializer = PipelineUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        pipeline = pipeline_service().update(
            self.principal(),
            pipeline_id,
            name=data.get("name"),
            make_default=bool(data.get("is_default")),
        )
        return Response(pipeline_to_dict(pipeline))


class PipelineStagesView(_Scoped):
    @extend_schema(
        tags=_PIPELINE_TAGS,
        operation_id="pipelines_stage_add",
        summary="Add a stage",
        description="Inserts a stage at `position` (0-based; default at the end).",
        parameters=[_TENANT_HEADER],
        request=StageAddSerializer,
        responses={201: PipelineReadSerializer},
    )
    def post(self, request: Request, pipeline_id: UUID) -> Response:
        serializer = StageAddSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        pipeline = pipeline_service().add_stage(
            self.principal(),
            pipeline_id,
            StageDefinition(
                name=data["name"], probability=data["probability"], kind=StageKind(data["kind"])
            ),
            data.get("position"),
        )
        return Response(pipeline_to_dict(pipeline), status=status.HTTP_201_CREATED)


class PipelineStageDetailView(_Scoped):
    @extend_schema(
        tags=_PIPELINE_TAGS,
        operation_id="pipelines_stage_update",
        summary="Rename, re-weight, archive or restore a stage",
        description=(
            "`is_active: false` archives the stage: it stays in history and on closed deals "
            "but deals can no longer be moved to it. A stage with open deals cannot be "
            "archived (409 `stage_in_use`); a pipeline keeps at least one active open stage."
        ),
        parameters=[_TENANT_HEADER],
        request=StageUpdateSerializer,
        responses=PipelineReadSerializer,
    )
    def patch(self, request: Request, pipeline_id: UUID, stage_id: UUID) -> Response:
        serializer = StageUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        pipeline = pipeline_service().update_stage(
            self.principal(),
            pipeline_id,
            stage_id,
            name=data.get("name"),
            probability=data.get("probability"),
            is_active=data.get("is_active"),
        )
        return Response(pipeline_to_dict(pipeline))

    @extend_schema(
        tags=_PIPELINE_TAGS,
        operation_id="pipelines_stage_delete",
        summary="Delete a stage, moving its deals",
        description=(
            "Deletes a stage of a pipeline you may configure. If deals stand on it (any "
            "status), `move_to_stage_id` is required (400 `move_target_required`): another "
            "active stage of the same kind in this pipeline; every deal moves there and the "
            "move is written to its stage history. A stage that ever held a deal is archived "
            "(`archived: true`, its name stays in history); a never-used one is removed. "
            "The last active open, won or lost stage cannot be deleted."
        ),
        parameters=[
            _TENANT_HEADER,
            OpenApiParameter(
                "move_to_stage_id", UUID, OpenApiParameter.QUERY, description="Where deals go"
            ),
        ],
        request=None,
        responses=StageRemovalSerializer,
    )
    def delete(self, request: Request, pipeline_id: UUID, stage_id: UUID) -> Response:
        raw = request.query_params.get("move_to_stage_id")
        try:
            move_to = UUID(raw) if raw else None
        except ValueError as exc:
            raise DomainValidationError("move_to_stage_id must be a UUID.") from exc
        result = pipeline_service().delete_stage(
            self.principal(), pipeline_id, stage_id, move_to_stage_id=move_to
        )
        return Response(
            {
                "pipeline": pipeline_to_dict(result.pipeline),
                "moved_deals": result.moved_deals,
                "archived": result.archived,
            }
        )


class PipelineAdoptDealsView(_Scoped):
    @extend_schema(
        tags=_PIPELINE_TAGS,
        operation_id="pipelines_adopt_deals_preview",
        summary="Preview: department deals still in a company pipeline",
        description=(
            "For a department pipeline you may configure: open deals of the department's "
            "active members that stand in the company-wide pipeline `from_pipeline_id`, per "
            "source stage. Nothing moves."
        ),
        parameters=[
            _TENANT_HEADER,
            OpenApiParameter("from_pipeline_id", UUID, OpenApiParameter.QUERY, required=True),
        ],
        responses=AdoptionPreviewSerializer,
    )
    def get(self, request: Request, pipeline_id: UUID) -> Response:
        raw = request.query_params.get("from_pipeline_id") or ""
        try:
            from_pipeline_id = UUID(raw)
        except ValueError as exc:
            raise DomainValidationError("from_pipeline_id must be a UUID.") from exc
        preview = pipeline_service().adoption_preview(
            self.principal(), pipeline_id, from_pipeline_id
        )
        return Response(
            {
                "from_pipeline_id": str(preview.source.id),
                "from_pipeline_name": preview.source.name,
                "total": preview.total,
                "stages": [
                    {
                        "stage_id": str(stage.id),
                        "name": stage.name,
                        "kind": stage.kind.value,
                        "open_deals": preview.counts.get(stage.id, 0),
                    }
                    for stage in sorted(preview.source.stages, key=lambda s: s.position)
                ],
            }
        )

    @extend_schema(
        tags=_PIPELINE_TAGS,
        operation_id="pipelines_adopt_deals",
        summary="Move the department's open deals into this department pipeline",
        description=(
            "Explicit, confirmed transfer (nothing moves automatically): the open deals of the "
            "department's active members in the company-wide pipeline move into this "
            "department pipeline, each source stage onto the stage given in `stage_map` (an "
            "active open stage of this pipeline). Every stage that has such deals must be "
            "mapped (400 `stage_mapping_required`). Closed deals and other people's deals stay. "
            "Each move is written to the deal's stage history. Head of sales: own department "
            "only; director: any; managers: 403."
        ),
        parameters=[_TENANT_HEADER],
        request=AdoptDealsSerializer,
        responses=AdoptionResultSerializer,
    )
    def post(self, request: Request, pipeline_id: UUID) -> Response:
        serializer = AdoptDealsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            stage_map = {UUID(str(k)): v for k, v in data["stage_map"].items()}
        except ValueError as exc:
            raise DomainValidationError("stage_map keys must be stage UUIDs.") from exc
        result = pipeline_service().adopt_deals(
            self.principal(), pipeline_id, data["from_pipeline_id"], stage_map
        )
        return Response(
            {"moved": result.moved, "per_stage": {str(k): v for k, v in result.per_stage.items()}}
        )


class PipelineStageOrderView(_Scoped):
    @extend_schema(
        tags=_PIPELINE_TAGS,
        operation_id="pipelines_stage_order",
        summary="Reorder stages",
        description="`stage_ids` lists every stage of the pipeline once, in the new order.",
        parameters=[_TENANT_HEADER],
        request=StageOrderSerializer,
        responses=PipelineReadSerializer,
    )
    def put(self, request: Request, pipeline_id: UUID) -> Response:
        serializer = StageOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pipeline = pipeline_service().reorder_stages(
            self.principal(), pipeline_id, serializer.validated_data["stage_ids"]
        )
        return Response(pipeline_to_dict(pipeline))


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
            _CLOSED_FROM,
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
            closed_from=_as_date(q.get("closed_from")),
            attention=attention,
            today=self._company_today(),
            ordering=q.get("ordering"),
        )
        page = deal_service().list_deals(self.principal(), filters, limit=limit, offset=offset)
        tasks = next_tasks(self.tenant.db_alias, (d.id for d in page.items))
        return Response(
            {
                "count": page.total,
                "results": [_with_next_task(deal_to_dict(d), tasks.get(d.id)) for d in page.items],
            }
        )

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


def _visible_customer(principal: Principal, customer_id: UUID | None) -> dict[str, Any] | None:
    """The linked customer under the customers module's own visibility rules."""
    if customer_id is None:
        return None
    try:
        customer = customer_service().get(principal, customer_id)
    except NotFoundError:
        return None
    return {
        "id": str(customer.id),
        "name": customer.name,
        "email": str(customer.email) if customer.email else None,
        "phone": str(customer.phone) if customer.phone else None,
    }


class DealDetailView(_Scoped):
    @extend_schema(
        tags=_DEAL_TAGS,
        operation_id="deals_retrieve",
        summary="Get one deal",
        description=(
            "Returns a deal by id, or 404 if it does not exist or is not visible to you. "
            "Adds the linked `customer` (null when none is linked or you cannot view it) "
            "and the `owner`'s display name."
        ),
        parameters=[_TENANT_HEADER],
        responses=DealDetailSerializer,
    )
    def get(self, request: Request, deal_id: UUID) -> Response:
        principal = self.principal()
        deal = deal_service().get(principal, deal_id)
        data = _with_next_task(
            deal_to_dict(deal), next_tasks(self.tenant.db_alias, [deal.id]).get(deal.id)
        )
        data["customer"] = _visible_customer(principal, deal.customer_id)
        data["owner"] = {
            "id": str(deal.owner_user_id),
            "name": member_display_name(principal.tenant_id, deal.owner_user_id) or "",
        }
        return Response(data)

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
            "and requires `reason`. An `open`-kind stage just moves the deal.\n\n"
            "System stages: «Продажа» (`system_code=sale`) is refused with 409 "
            "`sale_requires_payment` — a sale is closed by recording a confirmed payment. "
            "«Встреча перенесена» (`meeting_rescheduled`) needs `meeting_start`/`meeting_end` "
            "(400 `meeting_date_required`) and moves the deal's nearest scheduled meeting "
            "(or books one for the deal owner) in the same transaction. Any other new open "
            "stage needs `next_step` unless the deal already has an open task with a due "
            "date (400 `next_step_required`)."
        ),
        parameters=[_TENANT_HEADER],
        request=DealMoveSerializer,
        responses=DealReadSerializer,
    )
    def post(self, request: Request, deal_id: UUID) -> Response:
        serializer = DealMoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        deal = deal_service().move_stage(
            self.principal(),
            deal_id,
            data["stage_id"],
            reason=data.get("reason"),
            loss_reason_id=data.get("loss_reason_id"),
            extras=StageMoveExtras(
                meeting_start=data.get("meeting_start"),
                meeting_end=data.get("meeting_end"),
                next_step=next_step_from(data.get("next_step")),
            ),
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
        summary="Mark a deal won (refused)",
        description=(
            "Always 409 `sale_requires_payment`: a deal is won only when a confirmed payment "
            "moves it to «Продажа» (payments)."
        ),
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


class DealAssignView(_Scoped):
    @extend_schema(
        tags=_DEAL_TAGS,
        operation_id="deals_assign",
        summary="Change the responsible member",
        description=(
            "A manager cannot hand deals over; a head of sales hands them within their "
            "department; a director to any active member. The new owner's department "
            "becomes the deal's team. Works for open and closed deals."
        ),
        parameters=[_TENANT_HEADER],
        request=DealAssignSerializer,
        responses=DealReadSerializer,
    )
    def post(self, request: Request, deal_id: UUID) -> Response:
        serializer = DealAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        deal = deal_service().assign(
            self.principal(), deal_id, serializer.validated_data["owner_user_id"]
        )
        return Response(deal_to_dict(deal))


class DealHistoryView(_Scoped):
    @extend_schema(
        tags=_DEAL_TAGS,
        operation_id="deals_history",
        summary="Deal change history",
        description=(
            "Audited changes, newest first: action (deal.created, deal.updated, "
            "deal.stage_changed, deal.won, deal.lost, deal.reopened, deal.reassigned), "
            "who and when, and which fields changed (names only, never values). Stage "
            "transitions with reasons are in `stage-history/`."
        ),
        parameters=[_TENANT_HEADER],
        responses=None,
    )
    def get(self, request: Request, deal_id: UUID) -> Response:
        entries = deal_service().history(self.principal(), deal_id)
        tenant_id = self.membership.tenant_id
        names: dict[UUID, str | None] = {}
        results = []
        for e in entries:
            actor = e.actor_user_id
            if actor is not None and actor not in names:
                names[actor] = member_display_name(tenant_id, actor)
            results.append(
                {
                    "action": e.action,
                    "occurred_at": e.occurred_at.isoformat(),
                    "actor": {"id": str(actor), "name": names[actor]} if actor else None,
                    "changed_fields": list(e.changed_fields),
                    # A reassignment names the previous and the new owner
                    "details": people_in_details(tenant_id, e.details),
                }
            )
        return Response({"count": len(results), "results": results})


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


def _with_next_task(data: dict[str, Any], task: tuple[str, date] | None) -> dict[str, Any]:
    """`next_task`: the deal's nearest open dated task — its next step (null: none planned)."""
    data["next_task"] = {"title": task[0], "due_date": task[1].isoformat()} if task else None
    return data
