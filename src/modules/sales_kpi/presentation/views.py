from __future__ import annotations

from datetime import UTC, date, datetime

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.sales_kpi.composition import analytics_service
from modules.sales_kpi.domain.kpi import Period
from platform_core.presentation.tenancy import TenantScopedAPIView
from shared.domain.errors import DomainValidationError

_TENANT_HEADER = OpenApiParameter(
    "X-Tenant",
    str,
    OpenApiParameter.HEADER,
    required=True,
    description="Short name (slug) of your company, e.g. `demo`.",
    examples=[OpenApiExample("Demo tenant", value="demo")],
)
_PERIOD = OpenApiParameter(
    "period",
    str,
    OpenApiParameter.QUERY,
    description="Period length (default month).",
    enum=[p.value for p in Period],
)
_START = OpenApiParameter(
    "start",
    OpenApiTypes.DATE,
    OpenApiParameter.QUERY,
    description="First day of the period (YYYY-MM-DD; default today).",
)
_END = OpenApiParameter(
    "end",
    OpenApiTypes.DATE,
    OpenApiParameter.QUERY,
    description="History only: last period start to include (YYYY-MM-DD).",
)
_TAGS = ["Analytics"]


class _Scoped(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
            department_id=self.membership.department_id,
        )


def _period(request: Request) -> Period:
    value = request.query_params.get("period", Period.MONTH.value)
    if value not in {p.value for p in Period}:
        raise DomainValidationError("Invalid period; use day, week or month.")
    return Period(value)


def _date(value: str | None, *, default: date) -> date:
    if not value:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise DomainValidationError("Invalid date; use YYYY-MM-DD.") from None


class KpiView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="analytics_kpis",
        summary="Current KPIs for a period",
        description=(
            "Computes company-wide KPIs (deals created/won, revenue, win rate, leads "
            "captured, lead conversion rate, activities, tasks completed) over the chosen "
            "period from the event-driven read model. Requires the reports.view permission."
        ),
        parameters=[_TENANT_HEADER, _PERIOD, _START],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request) -> Response:
        period = _period(request)
        start = _date(request.query_params.get("start"), default=datetime.now(UTC).date())
        values = analytics_service().kpis(self.principal(), period, start)
        return Response(
            {
                "period": period.value,
                "start": start.isoformat(),
                "kpis": [
                    {
                        "code": v.code,
                        "name": v.name,
                        "aggregation": v.aggregation,
                        "unit": v.unit,
                        "value": str(v.value) if v.value is not None else None,
                    }
                    for v in values
                ],
            }
        )


class KpiHistoryView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="analytics_kpi_history",
        summary="KPI history (snapshots)",
        description=(
            "Returns stored KPI snapshots for the period between `start` and `end` "
            "(inclusive). Snapshots are produced by the scheduled snapshot job."
        ),
        parameters=[_TENANT_HEADER, _PERIOD, _START, _END],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request) -> Response:
        period = _period(request)
        today = datetime.now(UTC).date()
        start = _date(request.query_params.get("start"), default=today)
        end = _date(request.query_params.get("end"), default=today)
        snapshots = analytics_service().history(self.principal(), period, start, end)
        return Response(
            {
                "period": period.value,
                "snapshots": [
                    {
                        "kpi_code": s.kpi_code,
                        "period_start": s.period_start.isoformat(),
                        "value": str(s.value) if s.value is not None else None,
                        "computed_at": s.computed_at.isoformat(),
                    }
                    for s in snapshots
                ],
            }
        )
