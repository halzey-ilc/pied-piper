from __future__ import annotations

from typing import Any
from uuid import UUID

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.organizations.composition import organization_service
from modules.organizations.presentation.serializers import (
    CompanySerializer,
    DepartmentSerializer,
    HireRequestSerializer,
    RequestDecisionSerializer,
    StaffCreatedSerializer,
    StaffCreateSerializer,
    StaffNameSerializer,
    StaffSerializer,
    StaffUpdateSerializer,
    TemporaryPasswordSerializer,
    company_to_dict,
    department_to_dict,
    staff_request_to_dict,
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


class StaffNamesView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="staff_names",
        summary="Names of the company's staff",
        description=(
            "Every member of the company sees the names of its active staff, e.g. to show who "
            "owns a deal. Only `user_id` and `full_name`: contacts, roles and activity stay "
            "behind the staff list."
        ),
        parameters=[_TENANT_HEADER],
        responses=StaffNameSerializer(many=True),
    )
    def get(self, request: Request) -> Response:
        names = organization_service().member_names(self.principal())
        return Response(
            _page([{"user_id": str(user_id), "full_name": name} for user_id, name in names])
        )


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
                # direct: the director creates accounts; request: a head of sales asks the director
                "mode": options["mode"],
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


class StaffRequestsView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="staff_requests_list",
        summary="Hire and dismissal requests",
        description=(
            "The director sees the company's requests, a head of sales those of their "
            "department. Filter with ?status=pending|approved|rejected|cancelled."
        ),
        parameters=[_TENANT_HEADER],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request) -> Response:
        items = organization_service().staff_requests(
            self.principal(), request.query_params.get("status") or None
        )
        return Response(_page([staff_request_to_dict(r) for r in items]))

    @extend_schema(
        tags=_TAGS,
        operation_id="staff_requests_create",
        summary="Request a hire or a dismissal (head of sales)",
        description=(
            "A head of sales asks the director to hire into their own department (roles "
            "they may give) or to dismiss a member of their department. Nothing changes "
            "until the director approves."
        ),
        parameters=[_TENANT_HEADER],
        request=HireRequestSerializer,
        responses=OpenApiTypes.OBJECT,
    )
    def post(self, request: Request) -> Response:
        s = HireRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        service = organization_service()
        if d["kind"] == "hire":
            created = service.request_hire(
                self.principal(),
                first_name=d["first_name"],
                last_name=d.get("last_name", ""),
                role=d["role"],
                login=d.get("login") or None,
                email=d.get("email") or None,
                phone=d.get("phone", ""),
            )
        else:
            created = service.request_dismissal(
                self.principal(), d["membership_id"], d.get("reason", "")
            )
        return Response(staff_request_to_dict(created), status=status.HTTP_201_CREATED)


class StaffRequestApproveView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="staff_requests_approve",
        summary="Approve a request (director)",
        description=(
            "Hire: creates the account and returns its temporary password once. "
            "Dismissal: deactivates the account."
        ),
        parameters=[_TENANT_HEADER],
        request=None,
        responses=OpenApiTypes.OBJECT,
    )
    def post(self, request: Request, request_id: UUID) -> Response:
        decided, password = organization_service().approve_request(self.principal(), request_id)
        body: dict[str, Any] = {"request": staff_request_to_dict(decided)}
        if password is not None:
            body["temporary_password"] = password
        return Response(body)


class StaffRequestRejectView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="staff_requests_reject",
        summary="Reject a request (director)",
        parameters=[_TENANT_HEADER],
        request=RequestDecisionSerializer,
        responses=OpenApiTypes.OBJECT,
    )
    def post(self, request: Request, request_id: UUID) -> Response:
        s = RequestDecisionSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        decided = organization_service().reject_request(
            self.principal(), request_id, s.validated_data["note"]
        )
        return Response(staff_request_to_dict(decided))


class StaffRequestCancelView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="staff_requests_cancel",
        summary="Withdraw your own pending request",
        parameters=[_TENANT_HEADER],
        request=None,
        responses=OpenApiTypes.OBJECT,
    )
    def post(self, request: Request, request_id: UUID) -> Response:
        decided = organization_service().cancel_request(self.principal(), request_id)
        return Response(staff_request_to_dict(decided))
