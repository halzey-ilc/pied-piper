from __future__ import annotations

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.operations.composition import operations_service
from modules.operations.domain.rules import Thresholds
from modules.operations.presentation.serializers import (
    OperationsReportSerializer,
    ThresholdsSerializer,
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
_TAGS = ["Operations"]


class _Scoped(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
            department_id=self.membership.department_id,
        )


class FindingsView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="operations_findings",
        summary="Work falling through the cracks",
        description=(
            "Forgotten leads, deals without a next step, overdue tasks and stalled deals, "
            "computed now by deterministic rules over the records you can see (own/team/all "
            "by role). Oldest first, at most 500 items. Contains ids only."
        ),
        parameters=[_TENANT_HEADER],
        responses=OperationsReportSerializer,
    )
    def get(self, request: Request) -> Response:
        return Response(report_to_dict(operations_service().report(self.principal())))


class SettingsView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="operations_settings_get",
        summary="Company operations thresholds",
        parameters=[_TENANT_HEADER],
        responses=ThresholdsSerializer,
    )
    def get(self, request: Request) -> Response:
        return Response(thresholds_to_dict(operations_service().thresholds(self.principal())))

    @extend_schema(
        tags=_TAGS,
        operation_id="operations_settings_update",
        summary="Change company operations thresholds",
        description="Requires the operations.manage permission (owner, admin).",
        parameters=[_TENANT_HEADER],
        request=ThresholdsSerializer,
        responses=ThresholdsSerializer,
    )
    def put(self, request: Request) -> Response:
        serializer = ThresholdsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        saved = operations_service().update_thresholds(
            self.principal(), Thresholds(**serializer.validated_data)
        )
        return Response(thresholds_to_dict(saved))
