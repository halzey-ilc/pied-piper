from __future__ import annotations

from uuid import UUID

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.customers.application.service import CreateCustomer, UpdateCustomer
from modules.customers.composition import customer_service
from modules.customers.presentation.serializers import (
    CustomerCreateSerializer,
    CustomerListSerializer,
    CustomerReadSerializer,
    CustomerUpdateSerializer,
    customer_to_dict,
)
from platform_core.presentation.tenancy import TenantScopedAPIView

_TENANT_HEADER = OpenApiParameter(
    "X-Tenant",
    str,
    OpenApiParameter.HEADER,
    required=True,
    description=(
        "Short name (slug) of your company, e.g. `demo`. Identifies which company's data "
        "to use. Get it from GET /api/v1/me/. Omitting it returns 404 by design."
    ),
    examples=[OpenApiExample("Demo tenant", value="demo")],
)
_LIMIT = OpenApiParameter(
    "limit", int, OpenApiParameter.QUERY, description="Page size, 1-200 (default 50)."
)
_OFFSET = OpenApiParameter(
    "offset", int, OpenApiParameter.QUERY, description="Rows to skip for paging (default 0)."
)
_SEARCH = OpenApiParameter(
    "search",
    str,
    OpenApiParameter.QUERY,
    description="Case-insensitive match on name or email, or by the digits of the phone.",
)

_CUSTOMER_EXAMPLE = OpenApiExample(
    "Customer",
    value={
        "id": "5ac5bc6d-7451-46d5-aeea-49d57286b52b",
        "name": "Acme LLP",
        "email": "acme@example.test",
        "phone": None,
        "owner_user_id": "f6d54480-3330-451e-90ac-6e8c621900c7",
        "version": 1,
        "created_at": "2026-10-27T16:45:41Z",
    },
    response_only=True,
)

MAX_PAGE_SIZE = 200


class CustomerBaseView(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
            department_id=self.membership.department_id,
        )


class CustomerListCreateView(CustomerBaseView):
    @extend_schema(
        tags=["Customers"],
        operation_id="customers_list",
        summary="List customers",
        description=(
            "Returns customers visible to you in the selected company. What you see depends "
            "on your role: owners/admins and viewers see all, managers see their team, sales "
            "reps see only their own. Results are paged with `limit` and `offset`."
        ),
        parameters=[_TENANT_HEADER, _SEARCH, _LIMIT, _OFFSET],
        responses=CustomerListSerializer,
    )
    def get(self, request: Request) -> Response:
        try:
            limit = min(max(int(request.query_params.get("limit", 50)), 1), MAX_PAGE_SIZE)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except ValueError:
            limit, offset = 50, 0
        search = (request.query_params.get("search") or "").strip() or None
        page = customer_service().list_customers(
            self.principal(), limit=limit, offset=offset, search=search
        )
        return Response({"count": page.total, "results": [customer_to_dict(c) for c in page.items]})

    @extend_schema(
        tags=["Customers"],
        operation_id="customers_create",
        summary="Create a customer",
        description=(
            "Creates a customer in the selected company; you become its owner. `name` is "
            "required; `email` and `phone` are optional (phone must be E.164, e.g. "
            "`+77011234567`). Requires the customers.create permission for your role."
        ),
        parameters=[_TENANT_HEADER],
        request=CustomerCreateSerializer,
        responses={201: CustomerReadSerializer},
        examples=[
            OpenApiExample(
                "New customer",
                value={"name": "Acme LLP", "email": "acme@example.test"},
                request_only=True,
            ),
            _CUSTOMER_EXAMPLE,
        ],
    )
    def post(self, request: Request) -> Response:
        serializer = CustomerCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        customer = customer_service().create(
            self.principal(),
            CreateCustomer(
                name=data["name"], email=data.get("email") or None, phone=data.get("phone") or None
            ),
        )
        return Response(customer_to_dict(customer), status=status.HTTP_201_CREATED)


class CustomerDetailView(CustomerBaseView):
    @extend_schema(
        tags=["Customers"],
        operation_id="customers_retrieve",
        summary="Get one customer",
        description=(
            "Returns a single customer by id. If the customer does not exist, or is not "
            "visible to you, the response is 404 (the two cases are indistinguishable)."
        ),
        parameters=[_TENANT_HEADER],
        responses=CustomerReadSerializer,
        examples=[_CUSTOMER_EXAMPLE],
    )
    def get(self, request: Request, customer_id: UUID) -> Response:
        customer = customer_service().get(self.principal(), customer_id)
        return Response(customer_to_dict(customer))

    @extend_schema(
        tags=["Customers"],
        operation_id="customers_update",
        summary="Update a customer",
        description=(
            "Updates name, email or phone. Send only the fields you want to change. "
            "Optionally pass `version` for optimistic locking: if it does not match the "
            "stored version the response is 409, so you never overwrite a newer change."
        ),
        parameters=[_TENANT_HEADER],
        request=CustomerUpdateSerializer,
        responses=CustomerReadSerializer,
        examples=[
            OpenApiExample(
                "Rename", value={"name": "Acme Holdings", "version": 1}, request_only=True
            ),
            _CUSTOMER_EXAMPLE,
        ],
    )
    def patch(self, request: Request, customer_id: UUID) -> Response:
        serializer = CustomerUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        customer = customer_service().update(
            self.principal(),
            UpdateCustomer(
                customer_id=customer_id,
                expected_version=data.get("version"),
                name=data.get("name"),
                email=data.get("email"),
                phone=data.get("phone"),
            ),
        )
        return Response(customer_to_dict(customer))
