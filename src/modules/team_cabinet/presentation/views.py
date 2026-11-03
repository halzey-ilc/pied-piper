from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.conf import settings
from django.http import HttpResponse
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.organizations.composition import company_settings
from modules.team_cabinet.composition import team_cabinet_service
from modules.team_cabinet.domain.board import TrafficThresholds
from modules.team_cabinet.presentation.serializers import (
    REPORT_COLUMNS,
    PlanFactSerializer,
    TodayBoardSerializer,
    board_to_dict,
    member_card_to_dict,
    plan_fact_to_dict,
    team_report_to_dict,
)
from platform_core.presentation.tenancy import TenantScopedAPIView
from shared.domain.errors import DomainValidationError

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

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
            "Also per row: meetings still to confirm today and the nearest one, cancelled and "
            "date-less reschedules, tasks due/done today, the month plan and sales, and the day "
            "score (0-100 = up to 50 result vs the plan pro-rated by elapsed days + up to 50 CRM "
            "discipline; grades 5/4/3/2 from 90/75/60; null on a silent day or absence). "
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


_MONTH = OpenApiParameter(
    "month", str, OpenApiParameter.QUERY, description="Month YYYY-MM (default: current month)."
)


class TeamPlanFactView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="team_plan_fact",
        summary="Plan vs fact for a month",
        description=(
            "Per manager for the month: the monthly_sales plan target, actual sales (deals "
            "whose first payment they took), money received, remaining to plan, working days "
            "(calendar days for now) and a linear pace forecast — plus department totals. "
            "Sales count and received money are kept as separate figures. For a department "
            "(a head of sales, or a director with department_id) also its own monthly_sales "
            "target, remaining, percent and sales needed per remaining day. Needs "
            "team_cabinet.view."
        ),
        parameters=[_TENANT_HEADER, _MONTH, _DEPARTMENT],
        responses=PlanFactSerializer,
    )
    def get(self, request: Request) -> Response:
        timezone_name = self._timezone()
        month = request.query_params.get("month") or ""
        if not _MONTH_RE.match(month):
            today = datetime.now(ZoneInfo(timezone_name)).date()
            month = f"{today.year:04d}-{today.month:02d}"
        plan_fact = team_cabinet_service().plan_fact(
            self.principal(),
            month=month,
            timezone_name=timezone_name,
            department_id=self._department(request),
        )
        return Response(plan_fact_to_dict(plan_fact))


_DATE_FROM = OpenApiParameter("date_from", str, OpenApiParameter.QUERY, description="YYYY-MM-DD")
_DATE_TO = OpenApiParameter("date_to", str, OpenApiParameter.QUERY, description="YYYY-MM-DD")
_MAX_DAYS = 366


def _date(raw: str | None, default: date) -> date:
    if not raw:
        return default
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise DomainValidationError("Dates must be YYYY-MM-DD.") from exc


class TeamMemberCardView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="team_member_card",
        summary="Manager card for one-to-one",
        description=(
            "One manager over a period (default: the last 14 days incl. today): meetings "
            "booked/held/no-show and attendance (held / (held + no-show)), payments, sales "
            "(deals whose first payment fell in the period) and CR meeting→sale (sales / held), "
            "calls and stage changes per day with days without activity (working days, planned "
            "absences excluded), the current CRM tails, the month plan and sales, the same "
            "figures for the previous period of equal length and the department average per "
            "manager. Needs team_cabinet.view; a head of sales sees only their department."
        ),
        parameters=[_TENANT_HEADER, _DATE_FROM, _DATE_TO],
        responses=None,
    )
    def get(self, request: Request, user_id: UUID) -> Response:
        timezone_name = self._timezone()
        today = datetime.now(ZoneInfo(timezone_name)).date()
        end = _date(request.query_params.get("date_to"), today)
        start = _date(request.query_params.get("date_from"), end - timedelta(days=13))
        if start > end or (end - start).days > _MAX_DAYS:
            raise DomainValidationError("Period must be 1 to 366 days, start before end.")
        card = team_cabinet_service().member_card(
            self.principal(), user_id, start=start, end=end, timezone_name=timezone_name
        )
        return Response(member_card_to_dict(card))


_PERIOD = OpenApiParameter(
    "period", str, OpenApiParameter.QUERY, description="day | week | month (default day)."
)
_DATE = OpenApiParameter(
    "date", str, OpenApiParameter.QUERY, description="Any day inside the period (default today)."
)
_EXPORT = OpenApiParameter(
    "export", str, OpenApiParameter.QUERY, description="csv: download a CSV."
)


def _period_bounds(period: str, day: date) -> tuple[date, date]:
    if period == "week":
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=6)
    if period == "month":
        start = day.replace(day=1)
        nxt = (start + timedelta(days=32)).replace(day=1)
        return start, nxt - timedelta(days=1)
    return day, day


class TeamReportsView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="team_reports",
        summary="Day / week / month report of a department",
        description=(
            "Per manager for the period: meetings (booked, held, no-show, confirmed), calls, "
            "payments and received money (refunds not subtracted), prepayments, sales, done "
            "tasks, the current CRM tails and the month plan with sales so far; plus totals. "
            "Same definitions as the Today board and the manager card. `export=csv` returns a "
            "CSV without client data. Needs team_cabinet.view; a head of sales sees their "
            "department, a director any (department_id) or the whole company."
        ),
        parameters=[_TENANT_HEADER, _PERIOD, _DATE, _DEPARTMENT, _EXPORT],
        responses=None,
    )
    def get(self, request: Request) -> Response | HttpResponse:
        timezone_name = self._timezone()
        q = request.query_params
        period = q.get("period") if q.get("period") in {"day", "week", "month"} else "day"
        day = _date(q.get("date"), datetime.now(ZoneInfo(timezone_name)).date())
        start, end = _period_bounds(str(period), day)
        report = team_cabinet_service().team_report(
            self.principal(),
            start=start,
            end=end,
            timezone_name=timezone_name,
            department_id=self._department(request),
        )
        data = team_report_to_dict(report)
        data["period"] = period
        if q.get("export") != "csv":
            return Response(data)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(REPORT_COLUMNS)
        for row in data["rows"]:
            writer.writerow([row.get(column, "") for column in REPORT_COLUMNS])
        response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
        name = f"team-report-{period}-{start.isoformat()}.csv"
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        return response
