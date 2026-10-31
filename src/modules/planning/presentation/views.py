from __future__ import annotations

from datetime import date
from enum import StrEnum
from uuid import UUID

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.planning.application.ports import AbsenceFilter, PlanFilter
from modules.planning.application.service import CreateAbsence, SetPlan
from modules.planning.composition import planning_service
from modules.planning.domain.absence import AbsenceKind
from modules.planning.domain.plan import PlanMetric, PlanScope
from modules.planning.presentation.serializers import (
    AbsenceCreateSerializer,
    AbsenceListSerializer,
    AbsenceReadSerializer,
    PlanChangeListSerializer,
    PlanListSerializer,
    PlanReadSerializer,
    PlanSetSerializer,
    absence_to_dict,
    plan_change_to_dict,
    plan_to_dict,
)
from platform_core.presentation.tenancy import TenantScopedAPIView

_TENANT_HEADER = OpenApiParameter(
    "X-Tenant",
    str,
    OpenApiParameter.HEADER,
    required=True,
    description="Short name (slug) of your company, e.g. `demo`. Get it from GET /api/v1/me/.",
)
_LIMIT = OpenApiParameter(
    "limit", int, OpenApiParameter.QUERY, description="Page size 1-200 (default 50)."
)
_OFFSET = OpenApiParameter(
    "offset", int, OpenApiParameter.QUERY, description="Rows to skip (default 0)."
)
MAX_PAGE_SIZE = 200
_TAGS = ["Planning"]


class _Scoped(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
            department_id=self.membership.department_id,
        )

    def _paging(self, request: Request) -> tuple[int, int]:
        try:
            limit = min(max(int(request.query_params.get("limit", 50)), 1), MAX_PAGE_SIZE)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except ValueError:
            return 50, 0
        return limit, offset


def _uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _enum[E: StrEnum](enum_cls: type[E], value: str | None) -> E | None:
    if value and value in {m.value for m in enum_cls}:
        return enum_cls(value)
    return None


class PlanListSetView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="planning_plans_list",
        summary="List plans",
        description=(
            "Sales plans (targets per metric and period) at the company, department or "
            "manager level. A manager sees only their own plans. Filter by scope, scope_id, "
            "metric and period."
        ),
        parameters=[_TENANT_HEADER, _LIMIT, _OFFSET],
        responses=PlanListSerializer,
    )
    def get(self, request: Request) -> Response:
        limit, offset = self._paging(request)
        q = request.query_params
        filters = PlanFilter(
            scope=_enum(PlanScope, q.get("scope")),
            scope_id=_uuid(q.get("scope_id")),
            metric=_enum(PlanMetric, q.get("metric")),
            period=q.get("period") or None,
        )
        page = planning_service().list_plans(self.principal(), filters, limit=limit, offset=offset)
        return Response({"count": page.total, "results": [plan_to_dict(p) for p in page.items]})

    @extend_schema(
        tags=_TAGS,
        operation_id="planning_plans_set",
        summary="Set a plan",
        description=(
            "Creates or updates the target for a (scope, scope_id, metric, period); company "
            "and department plans are the director's, manager plans may also be set by a head "
            "of sales. Every change is recorded in the plan's history."
        ),
        parameters=[_TENANT_HEADER],
        request=PlanSetSerializer,
        responses={200: PlanReadSerializer},
    )
    def put(self, request: Request) -> Response:
        serializer = PlanSetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        plan = planning_service().set_plan(
            self.principal(),
            SetPlan(
                scope=PlanScope(data["scope"]),
                scope_id=data.get("scope_id"),
                metric=PlanMetric(data["metric"]),
                period=data["period"],
                target=data["target"],
            ),
        )
        return Response(plan_to_dict(plan))


class PlanHistoryView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="planning_plan_history",
        summary="Plan change history",
        description="Append-only history of a plan's target changes, oldest first.",
        parameters=[_TENANT_HEADER],
        responses=PlanChangeListSerializer,
    )
    def get(self, request: Request, plan_id: UUID) -> Response:
        changes = planning_service().plan_history(self.principal(), plan_id)
        return Response(
            {"count": len(changes), "results": [plan_change_to_dict(c) for c in changes]}
        )


class AbsenceListCreateView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="planning_absences_list",
        summary="List absences",
        description=(
            "Staff absences. Filter by user, kind and an overlap window "
            "(active_from/active_to). Requires the absences.manage permission."
        ),
        parameters=[_TENANT_HEADER, _LIMIT, _OFFSET],
        responses=AbsenceListSerializer,
    )
    def get(self, request: Request) -> Response:
        limit, offset = self._paging(request)
        q = request.query_params
        filters = AbsenceFilter(
            user_id=_uuid(q.get("user_id")),
            kind=_enum(AbsenceKind, q.get("kind")),
            active_from=_as_date(q.get("active_from")),
            active_to=_as_date(q.get("active_to")),
        )
        page = planning_service().list_absences(
            self.principal(), filters, limit=limit, offset=offset
        )
        return Response({"count": page.total, "results": [absence_to_dict(a) for a in page.items]})

    @extend_schema(
        tags=_TAGS,
        operation_id="planning_absences_create",
        summary="Record an absence",
        description=(
            "Records a staff absence (vacation/sick/day_off/business_trip/no_show). Requires "
            "the absences.manage permission."
        ),
        parameters=[_TENANT_HEADER],
        request=AbsenceCreateSerializer,
        responses={201: AbsenceReadSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = AbsenceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        absence = planning_service().create_absence(
            self.principal(),
            CreateAbsence(
                user_id=data["user_id"],
                kind=AbsenceKind(data["kind"]),
                start_date=data["start_date"],
                end_date=data["end_date"],
                comment=data.get("comment") or None,
            ),
        )
        return Response(absence_to_dict(absence), status=status.HTTP_201_CREATED)


def _as_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
