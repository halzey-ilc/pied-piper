from __future__ import annotations

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.risk.composition import risk_service
from modules.risk.domain.rules import Thresholds
from modules.risk.presentation.serializers import (
    RiskReportSerializer,
    RiskThresholdsSerializer,
    report_to_dict,
    thresholds_to_dict,
)
from platform_core.presentation.tenancy import TenantScopedAPIView

_TENANT_HEADER = OpenApiParameter(
    "X-Tenant",
    str,
    OpenApiParameter.HEADER,
    required=True,
    description="Short name (slug) of your company, e.g. `demo`.",
    examples=[OpenApiExample("Demo tenant", value="demo")],
)
_TAGS = ["Risk"]


class _Scoped(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
        )


class SignalsView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="risk_signals",
        summary="Signals worth a review",
        description=(
            "Deterministic rules over deals and the audit log of the last `window_days`: won "
            "deals without recorded activity, reopened deals, unusually large open deals, and "
            "bursts of disqualified leads, lost deals or voided invoices by one person. A "
            "signal is a reason to check, **not** evidence of a violation (`is_evidence` is "
            "always false). Ids and numbers only. Requires risk.view (owner, admin)."
        ),
        parameters=[_TENANT_HEADER],
        responses=RiskReportSerializer,
    )
    def get(self, request: Request) -> Response:
        return Response(report_to_dict(risk_service().report(self.principal())))


class SettingsView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="risk_settings_get",
        summary="Company risk thresholds",
        parameters=[_TENANT_HEADER],
        responses=RiskThresholdsSerializer,
    )
    def get(self, request: Request) -> Response:
        return Response(thresholds_to_dict(risk_service().thresholds(self.principal())))

    @extend_schema(
        tags=_TAGS,
        operation_id="risk_settings_update",
        summary="Change company risk thresholds",
        description="Requires risk.manage (owner, admin).",
        parameters=[_TENANT_HEADER],
        request=RiskThresholdsSerializer,
        responses=RiskThresholdsSerializer,
    )
    def put(self, request: Request) -> Response:
        serializer = RiskThresholdsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        saved = risk_service().update_thresholds(
            self.principal(), Thresholds(**serializer.validated_data)
        )
        return Response(thresholds_to_dict(saved))
