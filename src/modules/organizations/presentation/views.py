from __future__ import annotations

from typing import Any
from uuid import UUID

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.organizations.composition import organization_service
from modules.organizations.presentation.serializers import (
    CompanySerializer,
    DepartmentSerializer,
    StaffCreatedSerializer,
    StaffCreateSerializer,
    StaffSerializer,
    StaffUpdateSerializer,
    TemporaryPasswordSerializer,
    company_to_dict,
    department_to_dict,
    staff_to_dict,
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
_TAGS = ["Company and staff"]


class _Scoped(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
            department_id=self.membership.department_id,
        )


def _page(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"count": len(items), "results": items}


class CompanyView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="company_get",
        summary="Your company: name, country, currency, timezone",
        parameters=[_TENANT_HEADER],
        responses=CompanySerializer,
    )
    def get(self, request: Request) -> Response:
        return Response(company_to_dict(organization_service().company(self.principal())))

    @extend_schema(
        tags=_TAGS,
        operation_id="company_update",
        summary="Change company settings",
        description=(
            "Requires company.settings.manage (director). The timezone defines day, week and "
            "month boundaries of every report; the currency is the default for new deals."
        ),
        parameters=[_TENANT_HEADER],
        request=CompanySerializer,
        responses=CompanySerializer,
    )
    def patch(self, request: Request) -> Response:
        s = CompanySerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        company = organization_service().update_company(self.principal(), **s.validated_data)
        return Response(company_to_dict(company))


class DepartmentsView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="departments_list",
        summary="Departments of the company",
        parameters=[_TENANT_HEADER],
        responses=DepartmentSerializer(many=True),
    )
    def get(self, request: Request) -> Response:
        items = organization_service().departments(self.principal())
        return Response(_page([department_to_dict(d) for d in items]))

    @extend_schema(
        tags=_TAGS,
        operation_id="departments_create",
        summary="Create a department",
        description="Requires departments.manage (director).",
        parameters=[_TENANT_HEADER],
        request=DepartmentSerializer,
        responses={201: DepartmentSerializer},
    )
    def post(self, request: Request) -> Response:
        s = DepartmentSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = organization_service().create_department(self.principal(), s.validated_data["name"])
        return Response(department_to_dict(d), status=status.HTTP_201_CREATED)


class DepartmentDetailView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="departments_update",
        summary="Rename or deactivate a department",
        description="Departments are deactivated, never deleted.",
        parameters=[_TENANT_HEADER],
        request=DepartmentSerializer,
        responses=DepartmentSerializer,
    )
    def patch(self, request: Request, department_id: UUID) -> Response:
        s = DepartmentSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        d = organization_service().update_department(
            self.principal(), department_id, **s.validated_data
        )
        return Response(department_to_dict(d))


class StaffView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="staff_list",
        summary="Staff you can see",
        description="Director: the whole company; head of sales: their own department.",
        parameters=[_TENANT_HEADER],
        responses=StaffSerializer(many=True),
    )
    def get(self, request: Request) -> Response:
        items = organization_service().staff(self.principal())
        return Response(_page([staff_to_dict(m) for m in items]))

    @extend_schema(
        tags=_TAGS,
        operation_id="staff_create",
        summary="Create a staff account",
        description=(
            "The server generates a temporary password, returns it once and requires a change "
            "at first sign-in; nobody can read it later. A head of sales creates managers and "
            "lead callers in their own department (a sent department is ignored)."
        ),
        parameters=[_TENANT_HEADER],
        request=StaffCreateSerializer,
        responses={201: StaffCreatedSerializer},
    )
    def post(self, request: Request) -> Response:
        s = StaffCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        member, password = organization_service().create_member(
            self.principal(), **s.validated_data
        )
        return Response(
            {"member": staff_to_dict(member), "temporary_password": password},
            status=status.HTTP_201_CREATED,
        )


class StaffCreateOptionsView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="staff_create_options",
        summary="Roles and departments you may use when creating staff",
        parameters=[_TENANT_HEADER],
        responses=None,
    )
    def get(self, request: Request) -> Response:
        options = organization_service().create_options(self.principal())
        return Response(
            {
                "creatable_roles": options["creatable_roles"],
                "departments": [department_to_dict(d) for d in options["departments"]],  # type: ignore[attr-defined]
                "fixed_department_id": (
                    str(options["fixed_department_id"]) if options["fixed_department_id"] else None
                ),
            }
        )


class StaffDetailView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="staff_update",
        summary="Change a staff member's role or department",
        description="Only the director moves staff between departments.",
        parameters=[_TENANT_HEADER],
        request=StaffUpdateSerializer,
        responses=StaffSerializer,
    )
    def patch(self, request: Request, membership_id: UUID) -> Response:
        s = StaffUpdateSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        data = s.validated_data
        member = organization_service().update_member(
            self.principal(),
            membership_id,
            role=data.get("role"),
            department_id=data.get("department_id"),
            clear_department="department_id" in data and data["department_id"] is None,
        )
        return Response(staff_to_dict(member))


class StaffDeactivateView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="staff_deactivate",
        summary="Deactivate an account",
        description="Sign-in is blocked at once; history and records stay. Never deleted.",
        parameters=[_TENANT_HEADER],
        request=None,
        responses=StaffSerializer,
    )
    def post(self, request: Request, membership_id: UUID) -> Response:
        return Response(
            staff_to_dict(organization_service().deactivate(self.principal(), membership_id))
        )


class StaffResetPasswordView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="staff_reset_password",
        summary="Issue a new temporary password",
        description="Returned once; the member must change it at next sign-in.",
        parameters=[_TENANT_HEADER],
        request=None,
        responses=TemporaryPasswordSerializer,
    )
    def post(self, request: Request, membership_id: UUID) -> Response:
        password = organization_service().reset_password(self.principal(), membership_id)
        return Response({"temporary_password": password})
