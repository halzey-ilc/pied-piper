from __future__ import annotations

from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.organizations.composition import company_settings
from modules.payments.application.payment_service import RegisterPayment
from modules.payments.application.ports import InvoiceFilter, PaymentFilter
from modules.payments.application.service import CreateInvoice
from modules.payments.composition import invoice_service, payment_service
from modules.payments.domain.invoice import InvoiceStatus
from modules.payments.domain.payment import PaymentKind
from modules.payments.presentation.serializers import (
    InvoiceCreateSerializer,
    InvoiceListSerializer,
    InvoiceReadSerializer,
    PaymentRecordCreateSerializer,
    PaymentRecordListSerializer,
    PaymentRecordReadSerializer,
    PaymentSerializer,
    SalesMetricsSerializer,
    VoidSerializer,
    invoice_to_dict,
    metrics_to_dict,
    payment_to_dict,
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


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


_PAYMENT_KIND = OpenApiParameter(
    "kind",
    str,
    OpenApiParameter.QUERY,
    description="Filter by kind.",
    enum=[k.value for k in PaymentKind],
)
_PAID_FROM = OpenApiParameter(
    "paid_from", str, OpenApiParameter.QUERY, description="Paid on/after (YYYY-MM-DD)."
)
_PAID_TO = OpenApiParameter(
    "paid_to", str, OpenApiParameter.QUERY, description="Paid on/before (YYYY-MM-DD)."
)


class PaymentListCreateView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="payments_list",
        summary="List payments",
        description=(
            "Individual payment records (prepayments, instalments, full payments, refunds) "
            "visible to you. Filter by deal, customer, seller, kind, refund flag and paid-on "
            "date range; page with limit/offset."
        ),
        parameters=[
            _TENANT_HEADER,
            _DEAL,
            _CUSTOMER,
            _PAYMENT_KIND,
            _PAID_FROM,
            _PAID_TO,
            _LIMIT,
            _OFFSET,
        ],
        responses=PaymentRecordListSerializer,
    )
    def get(self, request: Request) -> Response:
        try:
            limit = min(max(int(request.query_params.get("limit", 50)), 1), MAX_PAGE_SIZE)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except ValueError:
            limit, offset = 50, 0
        q = request.query_params
        kind_value = q.get("kind")
        valid_kinds = {k.value for k in PaymentKind}
        refund = q.get("is_refund")
        filters = PaymentFilter(
            deal_id=_uuid(q.get("deal_id")),
            customer_id=_uuid(q.get("customer_id")),
            seller_user_id=_uuid(q.get("seller_user_id")),
            kind=PaymentKind(kind_value) if kind_value in valid_kinds else None,
            is_refund=(refund in {"1", "true", "True"}) if refund is not None else None,
            paid_from=_date(q.get("paid_from")),
            paid_to=_date(q.get("paid_to")),
        )
        page = payment_service().list_payments(
            self.principal(), filters, limit=limit, offset=offset
        )
        return Response({"count": page.total, "results": [payment_to_dict(p) for p in page.items]})

    @extend_schema(
        tags=_TAGS,
        operation_id="payments_register",
        summary="Register a payment",
        description=(
            "Records a received payment (or a refund) against a deal and/or a customer. "
            "`kind` is prepayment/additional/full. A `reference` makes registration "
            "idempotent: repeating it returns the first record instead of a duplicate. "
            "`currency` defaults to the company currency; `seller_user_id` to the recorder."
        ),
        parameters=[_TENANT_HEADER],
        request=PaymentRecordCreateSerializer,
        responses={201: PaymentRecordReadSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = PaymentRecordCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        payment = payment_service().register(
            self.principal(),
            RegisterPayment(
                amount=data["amount"],
                currency=data.get("currency")
                or company_settings(self.tenant.tenant_id)["currency"],
                kind=PaymentKind(data["kind"]),
                paid_on=data["paid_on"],
                deal_id=data.get("deal_id"),
                customer_id=data.get("customer_id"),
                seller_user_id=data.get("seller_user_id"),
                is_refund=data["is_refund"],
                reference=data.get("reference") or None,
            ),
        )
        return Response(payment_to_dict(payment), status=status.HTTP_201_CREATED)


class PaymentDetailView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="payments_retrieve",
        summary="Get one payment",
        description="Returns a payment by id, or 404 if it does not exist or is not visible.",
        parameters=[_TENANT_HEADER],
        responses=PaymentRecordReadSerializer,
    )
    def get(self, request: Request, payment_id: UUID) -> Response:
        return Response(payment_to_dict(payment_service().get(self.principal(), payment_id)))


class PaymentMetricsView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="payments_metrics",
        summary="Sales metrics for a period",
        description=(
            "Distinct figures for a period (default: the current month in the company "
            "timezone): sales_count (deals whose first payment fell in the period), turnover "
            "(their total value), received (money received, refunds subtracted) and debt "
            "(outstanding on those deals). Never collapsed into one number."
        ),
        parameters=[_TENANT_HEADER, _PAID_FROM, _PAID_TO],
        responses=SalesMetricsSerializer,
    )
    def get(self, request: Request) -> Response:
        settings = company_settings(self.tenant.tenant_id)
        today = datetime.now(ZoneInfo(str(settings["timezone"]))).date()
        paid_from = _date(request.query_params.get("paid_from")) or today.replace(day=1)
        paid_to = _date(request.query_params.get("paid_to")) or today
        metrics = payment_service().metrics(
            self.principal(),
            paid_from=paid_from,
            paid_to=paid_to,
            currency=str(settings["currency"]),
        )
        return Response(metrics_to_dict(metrics))


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
