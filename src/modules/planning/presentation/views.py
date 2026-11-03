from __future__ import annotations

from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.organizations.composition import company_settings
from modules.planning.application.ports import AbsenceFilter, PlanFilter
from modules.planning.application.service import (
    CreateAbsence,
    DistributePlan,
    MetricDistribution,
    PlanPart,
    SetPlan,
)
from modules.planning.composition import planning_service, work_session_service
from modules.planning.domain.absence import AbsenceKind
from modules.planning.domain.plan import PlanMetric, PlanScope
from modules.planning.domain.work_session import MAX_OPEN, WorkSession, WorkSessionStatus
from modules.planning.presentation.serializers import (
    AbsenceCreateSerializer,
    AbsenceListSerializer,
    AbsenceReadSerializer,
    PlanChangeListSerializer,
    PlanDistributeSerializer,
    PlanListSerializer,
    PlanReadSerializer,
    PlanSetSerializer,
    absence_to_dict,
    plan_change_to_dict,
    plan_to_dict,
    progress_to_dict,
)
from platform_core.presentation.tenancy import TenantScopedAPIView
from shared.domain.errors import DomainValidationError

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


class PlanProgressView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="planning_plans_progress",
        summary="Plan vs actual",
        description=(
            "Each visible plan (same filters and scope rules as the plan list) with its "
            "period dates, the actual result computed from the company's records and the "
            "completion percent (null when the target is 0). Formulas: monthly_sales — deals "
            "whose first payment falls in the month; monthly_turnover — payments minus "
            "refunds in the company currency; weekly_leads — leads created; "
            "weekly_prepayments — prepayments received (count); daily_calls — logged calls. "
            "A department plan counts its current members' results. `remaining` = target "
            "minus actual (not below 0); `?daily=true` adds the cumulative actual per day "
            "up to today (one query set per day: use with a narrow filter)."
        ),
        parameters=[_TENANT_HEADER, _LIMIT, _OFFSET],
        responses=None,
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
        settings = company_settings(self.membership.tenant_id)
        today = datetime.now(ZoneInfo(settings["timezone"])).date()
        page = planning_service().progress(
            self.principal(),
            filters,
            limit=limit,
            offset=offset,
            daily_until=today if q.get("daily") == "true" else None,
        )
        currency = settings["currency"]
        return Response(
            {"count": page.total, "results": [progress_to_dict(p, currency) for p in page.items]}
        )


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
                currency=data.get("currency") or None,
            ),
        )
        return Response(plan_to_dict(plan))


class PlanDistributeView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="planning_plans_distribute",
        summary="Distribute the company plan between departments",
        description=(
            "Director only. Body: `period` and `distributions` — one entry per metric "
            "(count and money together) with `parts` [{department_id, target}]; the "
            "single-metric shorthand `metric` + `parts` is also accepted. Everything is "
            "validated first and written in one transaction: if any metric is invalid "
            "nothing is saved. Each metric's parts must add up exactly to its company plan "
            "(400 otherwise, 400 if there is no company plan yet). Money parts use the "
            "company plan's currency; an optional `currency` on a money entry switches the "
            "company plan and all its department plans of that period to it. Each "
            "department plan keeps its change history; departments not listed keep their "
            "plans."
        ),
        parameters=[_TENANT_HEADER],
        request=PlanDistributeSerializer,
        responses={200: PlanListSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = PlanDistributeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        plans = planning_service().distribute_plan(
            self.principal(),
            DistributePlan(
                period=data["period"],
                distributions=tuple(
                    MetricDistribution(
                        metric=PlanMetric(item["metric"]),
                        parts=tuple(
                            PlanPart(department_id=part["department_id"], target=part["target"])
                            for part in item["parts"]
                        ),
                        currency=item.get("currency") or None,
                    )
                    for item in data["distributions"]
                ),
            ),
        )
        return Response({"count": len(plans), "results": [plan_to_dict(p) for p in plans]})


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


def work_session_to_dict(session: WorkSession) -> dict[str, Any]:
    return {
        "id": str(session.id),
        "user_id": str(session.user_id),
        "team_id": str(session.team_id) if session.team_id else None,
        "status": session.status.value,
        "started_at": session.started_at.isoformat(),
        # Only for an explicitly stopped session; "unconfirmed" ones have no known end.
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        "duration_seconds": session.duration_seconds,
        "settled_at": session.settled_at.isoformat() if session.settled_at else None,
    }


_WS_TAGS = ["Working time"]


class WorkSessionStartView(_Scoped):
    @extend_schema(
        tags=_WS_TAGS,
        operation_id="work_sessions_start",
        summary="Start my working session",
        description=(
            "Declares the start of work (not the login). 409 if a session is already open. "
            f"A session left open longer than {int(MAX_OPEN.total_seconds() // 3600)} h is "
            "marked `unconfirmed` (end unknown, not counted) before a new one starts."
        ),
        parameters=[_TENANT_HEADER],
        request=None,
        responses=None,
    )
    def post(self, request: Request) -> Response:
        session = work_session_service().start(self.principal())
        return Response(work_session_to_dict(session), status=status.HTTP_201_CREATED)


class WorkSessionStopView(_Scoped):
    @extend_schema(
        tags=_WS_TAGS,
        operation_id="work_sessions_stop",
        summary="Stop my working session",
        description="Closes the open session with its end time and duration; 409 if none.",
        parameters=[_TENANT_HEADER],
        request=None,
        responses=None,
    )
    def post(self, request: Request) -> Response:
        return Response(work_session_to_dict(work_session_service().stop(self.principal())))


class WorkSessionCurrentView(_Scoped):
    @extend_schema(
        tags=_WS_TAGS,
        operation_id="work_sessions_current",
        summary="My open working session",
        description="`{session: {...} | null}`.",
        parameters=[_TENANT_HEADER],
        responses=None,
    )
    def get(self, request: Request) -> Response:
        session = work_session_service().current(self.principal())
        return Response({"session": work_session_to_dict(session) if session else None})


class WorkSessionListView(_Scoped):
    @extend_schema(
        tags=_WS_TAGS,
        operation_id="work_sessions_list",
        summary="Working sessions history",
        description=(
            "Everyone sees their own; a head of sales also their department's members; the "
            "director the whole company (403 for anyone else's id). Filters: user_id, "
            "status (open | closed | unconfirmed), date_from / date_to (company-local days "
            "of the start). Newest first."
        ),
        parameters=[_TENANT_HEADER, _LIMIT, _OFFSET],
        responses=None,
    )
    def get(self, request: Request) -> Response:
        limit, offset = self._paging(request)
        q = request.query_params
        zone = ZoneInfo(company_settings(self.membership.tenant_id)["timezone"])
        try:
            start = date.fromisoformat(q["date_from"]) if q.get("date_from") else None
            end = date.fromisoformat(q["date_to"]) if q.get("date_to") else None
        except ValueError as exc:
            raise DomainValidationError("Dates are YYYY-MM-DD.") from exc
        items, total = work_session_service().history(
            self.principal(),
            user_id=_uuid(q.get("user_id")),
            status=_enum(WorkSessionStatus, q.get("status")),
            started_from=datetime.combine(start, time.min, tzinfo=zone) if start else None,
            started_before=(
                datetime.combine(end + timedelta(days=1), time.min, tzinfo=zone) if end else None
            ),
            limit=limit,
            offset=offset,
        )
        return Response({"count": total, "results": [work_session_to_dict(w) for w in items]})
