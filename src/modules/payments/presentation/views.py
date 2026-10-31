from __future__ import annotations

from uuid import UUID

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.organizations.composition import company_settings
from modules.payments.application.ports import InvoiceFilter
from modules.payments.application.service import CreateInvoice
from modules.payments.composition import invoice_service
from modules.payments.domain.invoice import InvoiceStatus
from modules.payments.presentation.serializers import (
    InvoiceCreateSerializer,
    InvoiceListSerializer,
    InvoiceReadSerializer,
    PaymentSerializer,
    VoidSerializer,
    invoice_to_dict,
)
from platform_core.presentation.tenancy import TenantScopedAPIView

_TENANT_HEADER = OpenApiParameter(
    "X-Tenant",
    str,
    OpenApiParameter.HEADER,
    required=True,
    description="Short name (slug) of your company, e.g. `demo`.",
)
_STATUS = OpenApiParameter(
    "status",
    str,
    OpenApiParameter.QUERY,
    description="Filter by status.",
    enum=[s.value for s in InvoiceStatus],
)
_CUSTOMER = OpenApiParameter(
    "customer_id", str, OpenApiParameter.QUERY, description="Filter by customer."
)
_DEAL = OpenApiParameter("deal_id", str, OpenApiParameter.QUERY, description="Filter by deal.")
_LIMIT = OpenApiParameter(
    "limit", int, OpenApiParameter.QUERY, description="Page size 1-200 (default 50)."
)
_OFFSET = OpenApiParameter(
    "offset", int, OpenApiParameter.QUERY, description="Rows to skip (default 0)."
)

MAX_PAGE_SIZE = 200
_TAGS = ["Payments"]


class _Scoped(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
            department_id=self.membership.department_id,
        )


def _uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


class InvoiceListCreateView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="invoices_list",
        summary="List invoices",
        description="Invoices visible to you (own/team/all by role), newest first.",
        parameters=[_TENANT_HEADER, _STATUS, _CUSTOMER, _DEAL, _LIMIT, _OFFSET],
        responses=InvoiceListSerializer,
    )
    def get(self, request: Request) -> Response:
        try:
            limit = min(max(int(request.query_params.get("limit", 50)), 1), MAX_PAGE_SIZE)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except ValueError:
            limit, offset = 50, 0
        status_value = request.query_params.get("status")
        valid = {s.value for s in InvoiceStatus}
        filters = InvoiceFilter(
            status=InvoiceStatus(status_value) if status_value in valid else None,
            customer_id=_uuid(request.query_params.get("customer_id")),
            deal_id=_uuid(request.query_params.get("deal_id")),
        )
        page = invoice_service().list_invoices(
            self.principal(), filters, limit=limit, offset=offset
        )
        return Response({"count": page.total, "results": [invoice_to_dict(i) for i in page.items]})

    @extend_schema(
        tags=_TAGS,
        operation_id="invoices_create",
        summary="Create a draft invoice",
        description=(
            "Creates a draft invoice. `amount` is positive; `currency` a 3-letter code. "
            "Optionally link an existing customer or deal, and set a due date. No card or bank "
            "data is stored. Issue it before recording payments."
        ),
        parameters=[_TENANT_HEADER],
        request=InvoiceCreateSerializer,
        responses={201: InvoiceReadSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = InvoiceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        invoice = invoice_service().create(
            self.principal(),
            CreateInvoice(
                amount=data["amount"],
                currency=data.get("currency")
                or company_settings(self.tenant.tenant_id)["currency"],
                number=data.get("number"),
                customer_id=data.get("customer_id"),
                deal_id=data.get("deal_id"),
                due_date=data.get("due_date"),
            ),
        )
        return Response(invoice_to_dict(invoice), status=status.HTTP_201_CREATED)


class InvoiceDetailView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="invoices_retrieve",
        summary="Get one invoice",
        description="Returns an invoice by id, or 404 if not found or not visible to you.",
        parameters=[_TENANT_HEADER],
        responses=InvoiceReadSerializer,
    )
    def get(self, request: Request, invoice_id: UUID) -> Response:
        return Response(invoice_to_dict(invoice_service().get(self.principal(), invoice_id)))


class InvoiceIssueView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="invoices_issue",
        summary="Issue an invoice",
        description="Moves a draft invoice to issued; after that payments can be recorded.",
        parameters=[_TENANT_HEADER],
        request=None,
        responses=InvoiceReadSerializer,
    )
    def post(self, request: Request, invoice_id: UUID) -> Response:
        return Response(invoice_to_dict(invoice_service().issue(self.principal(), invoice_id)))


class InvoicePaymentView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="invoices_record_payment",
        summary="Record a payment",
        description=(
            "Records a payment against an issued invoice. The currency must match and the "
            "amount must not exceed the outstanding balance. The invoice becomes paid when "
            "fully settled."
        ),
        parameters=[_TENANT_HEADER],
        request=PaymentSerializer,
        responses=InvoiceReadSerializer,
    )
    def post(self, request: Request, invoice_id: UUID) -> Response:
        serializer = PaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        invoice = invoice_service().record_payment(
            self.principal(), invoice_id, data["amount"], data["currency"]
        )
        return Response(invoice_to_dict(invoice))


class InvoiceVoidView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="invoices_void",
        summary="Void an invoice",
        description=(
            "Voids a draft or issued invoice with a required reason. "
            "Paid invoices cannot be voided."
        ),
        parameters=[_TENANT_HEADER],
        request=VoidSerializer,
        responses=InvoiceReadSerializer,
    )
    def post(self, request: Request, invoice_id: UUID) -> Response:
        serializer = VoidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invoice = invoice_service().void(
            self.principal(), invoice_id, serializer.validated_data["reason"]
        )
        return Response(invoice_to_dict(invoice))
