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
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.organizations.composition import company_settings
from modules.team_cabinet.composition import my_day_service, team_cabinet_service
from modules.team_cabinet.domain.actions import DEFAULT_ACTIONS_LIMIT, MAX_ACTIONS_LIMIT
from modules.team_cabinet.domain.board import TrafficThresholds
from modules.team_cabinet.presentation.serializers import (
    REPORT_COLUMNS,
    PlanFactSerializer,
    TodayBoardSerializer,
    board_to_dict,
    funnel_to_dict,
    losses_to_dict,
    member_card_to_dict,
    my_month_to_dict,
    plan_fact_to_dict,
    score_history_to_dict,
    team_report_to_dict,
    today_actions_to_dict,
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
_INCLUDE = OpenApiParameter(
    "include",
    str,
    OpenApiParameter.QUERY,
    description="`actions`: also return «Что делать сегодня» (the `actions` object).",
)
_DAY = OpenApiParameter(
    "date",
    OpenApiTypes.DATE,
    OpenApiParameter.QUERY,
    description="A past company-local day (YYYY-MM-DD), e.g. yesterday; default today.",
)
_ACTIONS_LIMIT = OpenApiParameter(
    "actions_limit",
    int,
    OpenApiParameter.QUERY,
    description=f"Items per action group, 1-{MAX_ACTIONS_LIMIT} (default {DEFAULT_ACTIONS_LIMIT}).",
)


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

    def _currency(self) -> str:
        """Money in cabinet responses is summed in this currency only."""
        return str(company_settings(self.tenant.tenant_id)["currency"])

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
            "With `include=actions` the response also has `actions` — «Что делать сегодня»: "
            "prioritised groups, each with a stable `kind`, its `rule` in words, the full "
            "`count`, counts per manager, money per currency (never summed across "
            "currencies) and up to `actions_limit` items (oldest problems first): "
            "payments_to_verify (pending_verification), overdue_tasks, tasks_due_soon (due "
            "today or tomorrow), meetings_without_outcome (ended, still scheduled), "
            "deals_without_next_step (no open task with a due date and no upcoming meeting), "
            "deals_to_push (on the stage for at least the company's stalled-deal days and with "
            "a held meeting, a confirmed prepayment or an unpaid balance — confirmed money in "
            "the deal currency only), leads_without_contact (new, no first response after 15 "
            "minutes) and managers_without_activity (sales managers and lead callers, not on "
            "absence, with no shift started today or no activity). Not tied to the operations "
            "plan gate. Needs the team_cabinet.view permission."
        ),
        parameters=[_TENANT_HEADER, _DEPARTMENT, _DAY, _INCLUDE, _ACTIONS_LIMIT],
        responses=TodayBoardSerializer,
    )
    def get(self, request: Request) -> Response:
        service = team_cabinet_service()
        principal = self.principal()
        timezone_name = self._timezone()
        department_id = self._department(request)
        board = service.today_board(
            principal,
            timezone_name=timezone_name,
            thresholds=self._thresholds(),
            department_id=department_id,
            on=_optional_date(request.query_params.get("date")),
        )
        data = {**board_to_dict(board), "currency": self._currency()}
        include = {part.strip() for part in request.query_params.get("include", "").split(",")}
        if "actions" in include:
            actions = service.today_actions(
                principal,
                board,
                timezone_name=timezone_name,
                limit=_actions_limit(request.query_params.get("actions_limit")),
                department_id=department_id,
            )
            data["actions"] = today_actions_to_dict(actions)
        return Response(data)


def _actions_limit(raw: str | None) -> int:
    if not raw:
        return DEFAULT_ACTIONS_LIMIT
    try:
        value = int(raw)
    except ValueError as exc:
        raise DomainValidationError("actions_limit must be a number.") from exc
    if not 1 <= value <= MAX_ACTIONS_LIMIT:
        raise DomainValidationError(f"actions_limit must be 1 to {MAX_ACTIONS_LIMIT}.")
    return value


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
        return Response({**board_to_dict(board), "currency": self._currency()})


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
        return Response({**plan_fact_to_dict(plan_fact), "currency": self._currency()})


_DATE_FROM = OpenApiParameter("date_from", str, OpenApiParameter.QUERY, description="YYYY-MM-DD")
_DATE_TO = OpenApiParameter("date_to", str, OpenApiParameter.QUERY, description="YYYY-MM-DD")
_MAX_DAYS = 366


def _optional_date(raw: str | None) -> date | None:
    return _date(raw, date.min) if raw else None


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
        return Response({**member_card_to_dict(card), "currency": self._currency()})


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
        data = {**team_report_to_dict(report), "currency": self._currency()}
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


def _range(request: Request, timezone_name: str, default_days: int) -> tuple[date, date]:
    today = datetime.now(ZoneInfo(timezone_name)).date()
    end = _date(request.query_params.get("date_to"), today)
    start = _date(request.query_params.get("date_from"), end - timedelta(days=default_days - 1))
    if start > end or (end - start).days > _MAX_DAYS:
        raise DomainValidationError("Period must be 1 to 366 days, start before end.")
    return start, end


_PIPELINE = OpenApiParameter(
    "pipeline_id",
    str,
    OpenApiParameter.QUERY,
    description="Pipeline (default: the department's default, else the company default).",
)
_STALE_DAYS = OpenApiParameter(
    "stale_days",
    int,
    OpenApiParameter.QUERY,
    description="An open deal is stale after this many days on its stage (1-365, default 14).",
)
_DEFAULT_STALE_DAYS = 14


class TeamFunnelView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="team_funnel",
        summary="Funnel: stage conversion, stage age and the bottleneck",
        description=(
            "Stages of one pipeline. Now: open deals on each stage, their amount (company "
            "currency only), the median days since they entered the stage and how many sit "
            "there longer than `stale_days`. For the period (default: last 30 days): "
            "`reached` — deals that entered the stage in the period (creation on the stage "
            "or a move to it), `moved_on` — of those, how many later reached a later stage or "
            "won, `conversion` = moved_on / reached; `reached_previous` — the same for the "
            "previous period of equal length. Bottleneck: the open stage with the lowest "
            "conversion among stages with at least 3 deals reached, else the one with the "
            "most stale deals. Also won/lost closings in the period and the previous one. "
            "Needs team_cabinet.view; a head of sales sees their department's deals."
        ),
        parameters=[_TENANT_HEADER, _PIPELINE, _DATE_FROM, _DATE_TO, _STALE_DAYS, _DEPARTMENT],
        responses=None,
    )
    def get(self, request: Request) -> Response:
        timezone_name = self._timezone()
        start, end = _range(request, timezone_name, 30)
        raw_pipeline = request.query_params.get("pipeline_id")
        raw_stale = request.query_params.get("stale_days")
        try:
            pipeline_id = UUID(raw_pipeline) if raw_pipeline else None
            stale_days = int(raw_stale) if raw_stale else _DEFAULT_STALE_DAYS
        except ValueError as exc:
            raise DomainValidationError("pipeline_id or stale_days is invalid.") from exc
        if not 1 <= stale_days <= 365:
            raise DomainValidationError("stale_days must be 1 to 365.")
        report = team_cabinet_service().funnel(
            self.principal(),
            start=start,
            end=end,
            timezone_name=timezone_name,
            stale_days=stale_days,
            pipeline_id=pipeline_id,
            department_id=self._department(request),
        )
        return Response({**funnel_to_dict(report), "currency": self._currency()})


class TeamLossesView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="team_losses",
        summary="Losses: loss reasons, meeting cancellations and no-shows, lead response",
        description=(
            "For the period (default: last 90 days) and per month in the company timezone: "
            "deals closed as lost by loss reason (the company list, else the free-text reason, "
            "else «Причина не указана»); meetings cancelled by cancel reason and no-shows by "
            "no-show reason (both by the meeting's start time); counts for the previous period "
            "of equal length. Lead response: leads created in the period, how many got a first "
            "response (the earliest activity on the lead, or its qualification, "
            "disqualification, conversion or edit by a person) and the median minutes to it, "
            "with the count within 15 and 60 minutes. Needs team_cabinet.view; a head of sales "
            "sees their department."
        ),
        parameters=[_TENANT_HEADER, _DATE_FROM, _DATE_TO, _DEPARTMENT],
        responses=None,
    )
    def get(self, request: Request) -> Response:
        timezone_name = self._timezone()
        start, end = _range(request, timezone_name, 90)
        report = team_cabinet_service().losses(
            self.principal(),
            start=start,
            end=end,
            timezone_name=timezone_name,
            department_id=self._department(request),
        )
        return Response(losses_to_dict(report))


class TeamMemberScoresView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="team_member_scores",
        summary="Day score history of one manager",
        description=(
            "The day score (0-100, grade 5/4/3/2) for every day of the period (default: last "
            "30 days), computed with the Today board rule from that day's calls, meetings and "
            "payments and the month's plan and sales up to that day. Open and overdue tasks "
            "are as they stood at the end of the day; cancelled tasks are left out because "
            "their cancellation time is not stored. `points` is null with reason `silent` (no "
            "activity), `absent` (planned absence), or for days not yet over. Needs "
            "team_cabinet.view; a head of sales sees only their department."
        ),
        parameters=[_TENANT_HEADER, _DATE_FROM, _DATE_TO],
        responses=None,
    )
    def get(self, request: Request, user_id: UUID) -> Response:
        timezone_name = self._timezone()
        start, end = _range(request, timezone_name, 30)
        if (end - start).days > 92:
            raise DomainValidationError("Score history covers at most 93 days.")
        history = team_cabinet_service().score_history(
            self.principal(),
            user_id,
            start=start,
            end=end,
            timezone_name=timezone_name,
            thresholds=self._thresholds(),
        )
        return Response(score_history_to_dict(history))


class MyMonthView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="team_my_month",
        summary="My month («Мой день» of a sales manager)",
        description=(
            "The caller's own month (`month=YYYY-MM`, default the current one), in the company "
            "currency: sales (deals whose first confirmed payment fell in the month, credited "
            "to its seller) and last month's, new clients, turnover, debt and average check of "
            "those deals; confirmed money taken in the month by kind (prepayments, additional, "
            "full, refunds, received); own meetings dated in the month (assigned, passed, ahead, "
            "held, attendance = held/passed, sales per held meeting); the user's monthly_sales "
            "and monthly_turnover plan targets; `earnings` = salary + percent of the money "
            "received, by the director's terms (null until set); and a block per catalog product "
            "shown on «Мой день». Needs payments.view (own scope is enough)."
        ),
        parameters=[
            _TENANT_HEADER,
            OpenApiParameter("month", str, OpenApiParameter.QUERY, description="YYYY-MM"),
        ],
        responses=None,
    )
    def get(self, request: Request) -> Response:
        month = request.query_params.get("month") or None
        if month is not None and not _MONTH_RE.match(month):
            raise DomainValidationError("month must be YYYY-MM.")
        return Response(my_month_to_dict(my_day_service().my_month(self.principal(), month)))


class MyFocusView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="team_my_focus",
        summary="My focus (a sales manager's priority list)",
        description=(
            "The caller's own records that need work, in this order: miss_tasks (open "
            "«Работа над ошибками» tasks from the head of sales), overdue_tasks (other overdue "
            "tasks), deals_to_push, debts_to_collect (open or won deals with confirmed money "
            "below the amount), leads_without_contact, meetings_without_outcome, "
            "deals_without_next_step. Same shape and rules as `actions` of the today board. "
            "Needs activities.view (own scope is enough); not tied to the operations plan gate."
        ),
        parameters=[_TENANT_HEADER, _ACTIONS_LIMIT],
        responses=None,
    )
    def get(self, request: Request) -> Response:
        focus = my_day_service().my_focus(
            self.principal(), limit=_actions_limit(request.query_params.get("actions_limit"))
        )
        return Response(today_actions_to_dict(focus))
