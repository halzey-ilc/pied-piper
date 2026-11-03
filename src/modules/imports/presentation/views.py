from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.imports.composition import import_service
from modules.imports.domain.rows import ImportEntity
from modules.imports.presentation.serializers import (
    ImportRequestSerializer,
    job_to_dict,
    preview_to_dict,
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
_TAGS = ["Imports"]
_EXAMPLE = OpenApiExample(
    "Leads from a spreadsheet",
    value={
        "entity": "leads",
        "filename": "leads.csv",
        "content": "Имя;Телефон;E-mail;Источник\nАйгуль;+996555123456;;сайт\n",  # noqa: RUF001
    },
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


class ImportPreviewView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="imports_preview",
        summary="Check a CSV file before importing",
        description=(
            "Parses a CSV (comma, semicolon or tab; header row with Имя/Name, Телефон/Phone, "
            "E-mail, Источник/Source) and classifies every row: `new`, `duplicate` (the phone "
            "or e-mail already belongs to a lead or customer of the company, or repeats an "
            "earlier row) or `invalid` (with the reason). Nothing is saved. Phones must be "
            "international (+996…). Up to 2000 rows / 2 MB."
        ),
        parameters=[_TENANT_HEADER],
        request=ImportRequestSerializer,
        responses=OpenApiTypes.OBJECT,
        examples=[_EXAMPLE],
    )
    def post(self, request: Request) -> Response:
        s = ImportRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        preview = import_service().preview(
            self.principal(), ImportEntity(s.validated_data["entity"]), s.validated_data["content"]
        )
        return Response(preview_to_dict(preview))


class ImportListCreateView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="imports_list",
        summary="Recent imports",
        description="The company's last 20 imports with their counts and row errors.",
        parameters=[_TENANT_HEADER],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request) -> Response:
        jobs = import_service().recent(self.principal())
        return Response({"count": len(jobs), "results": [job_to_dict(j) for j in jobs]})

    @extend_schema(
        tags=_TAGS,
        operation_id="imports_create",
        summary="Import leads or customers from a CSV file",
        description=(
            "Creates the rows the preview marks as `new`, one by one through the lead or "
            "customer service (same rules, events and audit as manual creation); duplicates "
            "and invalid rows are skipped and reported. `owner_user_id` hands every created "
            "record to that member (only within your area of responsibility); default: you. "
            "The import is audited as `import.completed`."
        ),
        parameters=[_TENANT_HEADER],
        request=ImportRequestSerializer,
        responses={201: OpenApiTypes.OBJECT},
        examples=[_EXAMPLE],
    )
    def post(self, request: Request) -> Response:
        s = ImportRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        data = s.validated_data
        job = import_service().commit(
            self.principal(),
            ImportEntity(data["entity"]),
            data.get("filename") or "import.csv",
            data["content"],
            data.get("owner_user_id"),
        )
        return Response(job_to_dict(job), status=status.HTTP_201_CREATED)
