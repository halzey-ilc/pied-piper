from __future__ import annotations

from uuid import UUID

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.workspace.composition import workspace_service
from modules.workspace.domain.layout import Layout
from modules.workspace.presentation.serializers import (
    LayoutReadSerializer,
    LayoutUpdateSerializer,
    layout_to_dict,
)
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
_DEPARTMENT = OpenApiParameter(
    "department_id",
    str,
    OpenApiParameter.QUERY,
    description="A department's layout; omit for the company-wide layout (director only).",
)
_TAGS = ["Workspace"]


class _Scoped(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
            department_id=self.membership.department_id,
        )


def _department(request: Request) -> UUID | None:
    raw = request.query_params.get("department_id")
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError as exc:
        raise DomainValidationError("department_id must be a UUID.") from exc


class LayoutView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="workspace_layout_get",
        summary="Workspace layout of a scope",
        description=(
            "Which blocks of the managers' «Мой день» are shown and in what order — for the "
            "company (director) or a department (director; head of sales — own department). "
            "Unset scopes fall back to the company layout, then to the default."
        ),
        parameters=[_TENANT_HEADER, _DEPARTMENT],
        responses=LayoutReadSerializer,
    )
    def get(self, request: Request) -> Response:
        return Response(
            layout_to_dict(workspace_service().get(self.principal(), _department(request)))
        )

    @extend_schema(
        tags=_TAGS,
        operation_id="workspace_layout_set",
        summary="Set the workspace layout of a scope",
        description=(
            "Known block ids only, each once; blocks not listed are appended visible. Audited."
        ),
        parameters=[_TENANT_HEADER, _DEPARTMENT],
        request=LayoutUpdateSerializer,
        responses=LayoutReadSerializer,
    )
    def put(self, request: Request) -> Response:
        serializer = LayoutUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        layout = Layout.of([(b["id"], b["visible"]) for b in serializer.validated_data["blocks"]])
        saved = workspace_service().save(self.principal(), _department(request), layout)
        return Response(layout_to_dict(saved))


class EffectiveLayoutView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="workspace_layout_effective",
        summary="The workspace layout that applies to me",
        description="My department's layout, else the company's, else the default. Any member.",
        parameters=[_TENANT_HEADER],
        responses=LayoutReadSerializer,
    )
    def get(self, request: Request) -> Response:
        return Response(layout_to_dict(workspace_service().effective(self.principal())))
