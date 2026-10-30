from __future__ import annotations

from typing import Any
from uuid import UUID

from django.conf import settings
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.organizations.composition import company_settings
from modules.team_cabinet.composition import team_cabinet_service
from modules.team_cabinet.domain.board import TrafficThresholds
from modules.team_cabinet.presentation.serializers import TodayBoardSerializer, board_to_dict
from platform_core.presentation.tenancy import TenantScopedAPIView

_TENANT_HEADER = OpenApiParameter(
    "X-Tenant",
    str,
    OpenApiParameter.HEADER,
    required=True,
    description="Short name (slug) of your company, e.g. `demo`. Get it from GET /api/v1/me/.",
    examples=[OpenApiExample("Demo tenant", value="demo")],
)
_DEPARTMENT = OpenApiParameter(
    "department_id",
    str,
    OpenApiParameter.QUERY,
    description="Director only: limit to one department (a head of sales always sees their own).",
)
_TAGS = ["Cabinet"]


class _Scoped(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
            department_id=self.membership.department_id,
        )

    def _thresholds(self) -> TrafficThresholds:
        cfg: dict[str, Any] = settings.TEAM_CABINET
        return TrafficThresholds(
            meeting_outcome_minutes=cfg["MEETING_OUTCOME_MINUTES"],
            morning_confirm_deadline_hour=cfg["MORNING_CONFIRM_DEADLINE_HOUR"],
            morning_cutoff_hour=cfg["MORNING_CUTOFF_HOUR"],
            morning_confirm_ratio=cfg["MORNING_CONFIRM_RATIO"],
            yellow_rules_enabled=cfg["YELLOW_RULES_ENABLED"],
        )

    def _timezone(self) -> str:
        return str(company_settings(self.tenant.tenant_id)["timezone"])

    def _department(self, request: Request) -> UUID | None:
        raw = request.query_params.get("department_id")
        if not raw:
            return None
        try:
            return UUID(raw)
        except ValueError:
            return None


class TeamTodayView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="team_today",
        summary="Today board (head-of-sales cabinet)",
        description=(
            "Per-manager figures for today plus the department totals and a traffic light, "
            "computed live in the company timezone (`as_of` is the data cut). Each row has "
            "calls, meetings (confirmed/held/no-show/overdue-outcome), payments received, "
            "open/overdue tasks and whether the person is on absence. Approved RED rules: no "
            "activity on a working day, a meeting over 45 min old without an outcome, and "
            "fewer than 40% of morning meetings confirmed by 10:00. YELLOW/GREEN are not "
            "finalised, so status is RED or GREEN unless YELLOW rules are enabled in config. "
            "Needs the team_cabinet.view permission."
        ),
        parameters=[_TENANT_HEADER, _DEPARTMENT],
        responses=TodayBoardSerializer,
    )
    def get(self, request: Request) -> Response:
        board = team_cabinet_service().today_board(
            self.principal(),
            timezone_name=self._timezone(),
            thresholds=self._thresholds(),
            department_id=self._department(request),
        )
        return Response(board_to_dict(board))


class TeamInactivityView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="team_inactivity",
        summary="Managers with no activity today",
        description=(
            "The subset of the Today board with no calls, meetings or payments today and not "
            "on a planned absence (spec's 'no activity' rather than mere silence). Needs "
            "team_cabinet.view."
        ),
        parameters=[_TENANT_HEADER, _DEPARTMENT],
        responses=TodayBoardSerializer,
    )
    def get(self, request: Request) -> Response:
        board = team_cabinet_service().inactivity(
            self.principal(),
            timezone_name=self._timezone(),
            thresholds=self._thresholds(),
            department_id=self._department(request),
        )
        return Response(board_to_dict(board))
