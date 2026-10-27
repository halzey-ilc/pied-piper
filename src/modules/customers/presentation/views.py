from __future__ import annotations

from uuid import UUID

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.customers.application.service import CreateCustomer, UpdateCustomer
from modules.customers.composition import customer_service
from modules.customers.presentation.serializers import (
    CustomerCreateSerializer,
    CustomerUpdateSerializer,
    customer_to_dict,
)
from platform_core.presentation.tenancy import TenantScopedAPIView

MAX_PAGE_SIZE = 200


class CustomerBaseView(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
        )


class CustomerListCreateView(CustomerBaseView):
    def get(self, request: Request) -> Response:
        try:
            limit = min(max(int(request.query_params.get("limit", 50)), 1), MAX_PAGE_SIZE)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except ValueError:
            limit, offset = 50, 0
        page = customer_service().list_customers(self.principal(), limit=limit, offset=offset)
        return Response({"count": page.total, "results": [customer_to_dict(c) for c in page.items]})

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
    def get(self, request: Request, customer_id: UUID) -> Response:
        customer = customer_service().get(self.principal(), customer_id)
        return Response(customer_to_dict(customer))

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
