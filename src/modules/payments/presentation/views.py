from __future__ import annotations

from datetime import date, datetime
from urllib.parse import quote
from uuid import UUID
from zoneinfo import ZoneInfo

from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.organizations.composition import company_settings
from modules.payments.application.payment_service import (
    PaymentService,
    ReceiptUpload,
    RegisterPayment,
)
from modules.payments.application.ports import InvoiceFilter, PaymentFilter
from modules.payments.application.service import CreateInvoice
from modules.payments.composition import invoice_service, payment_service
from modules.payments.domain.invoice import InvoiceStatus
from modules.payments.domain.payment import Payment, PaymentKind, PaymentStatus
from modules.payments.domain.receipt import MAX_RECEIPT_BYTES, ReceiptRejectedError
from modules.payments.presentation.serializers import (
    DealPaymentSummarySerializer,
    InvoiceCreateSerializer,
    InvoiceListSerializer,
    InvoiceReadSerializer,
    PaymentRecordCreateSerializer,
    PaymentRecordListSerializer,
    PaymentRecordReadSerializer,
    PaymentRejectSerializer,
    PaymentSerializer,
    SalesMetricsSerializer,
    VoidSerializer,
    deal_summary_to_dict,
    invoice_to_dict,
    metrics_to_dict,
    payment_to_dict,
)
from platform_core.presentation.tenancy import TenantScopedAPIView
from shared.domain.errors import DomainValidationError

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


def _payments_to_dicts(
    service: PaymentService,
    principal: Principal,
    payments: list[Payment],
    *,
    with_deals: bool = False,
) -> list[dict[str, object]]:
    receipts = service.receipt_info(payments)
    deals = (
        service.deals([p.deal_id for p in payments if p.deal_id is not None]) if with_deals else {}
    )
    return [
        payment_to_dict(
            p,
            receipts.get(p.receipt_id) if p.receipt_id else None,
            can_verify=(
                p.status is PaymentStatus.PENDING_VERIFICATION and service.can_verify(principal, p)
            ),
            deal=deals.get(p.deal_id) if p.deal_id else None,
        )
        for p in payments
    ]


def _one(service: PaymentService, principal: Principal, payment: Payment) -> dict[str, object]:
    return _payments_to_dicts(service, principal, [payment])[0]


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
_DEPARTMENT = OpenApiParameter(
    "department_id", str, OpenApiParameter.QUERY, description="Only this sales department."
)


def _uuid_param(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError as exc:
        raise DomainValidationError("department_id must be a UUID.") from exc


_PAID_FROM = OpenApiParameter(
    "paid_from", str, OpenApiParameter.QUERY, description="Paid on/after (YYYY-MM-DD)."
)
_PAID_TO = OpenApiParameter(
    "paid_to", str, OpenApiParameter.QUERY, description="Paid on/before (YYYY-MM-DD)."
)
_PAYMENT_STATUS = OpenApiParameter(
    "status",
    str,
    OpenApiParameter.QUERY,
    description="Filter by verification status.",
    enum=[s.value for s in PaymentStatus],
)


class PaymentListCreateView(_Scoped):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(
        tags=_TAGS,
        operation_id="payments_list",
        summary="List payments",
        description=(
            "Individual payment records (prepayments, instalments, full payments, refunds) "
            "visible to you (and every payment of a deal you can see). Filter by deal, "
            "customer, seller, kind, refund flag, verification status and paid-on date range; "
            "page with limit/offset. Each payment carries its status, receipt metadata and "
            "`can_verify`."
        ),
        parameters=[
            _TENANT_HEADER,
            _DEAL,
            _PAYMENT_STATUS,
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
        status_value = q.get("status")
        filters = PaymentFilter(
            deal_id=_uuid(q.get("deal_id")),
            customer_id=_uuid(q.get("customer_id")),
            seller_user_id=_uuid(q.get("seller_user_id")),
            kind=PaymentKind(kind_value) if kind_value in valid_kinds else None,
            is_refund=(refund in {"1", "true", "True"}) if refund is not None else None,
            paid_from=_date(q.get("paid_from")),
            paid_to=_date(q.get("paid_to")),
            status=(
                PaymentStatus(status_value)
                if status_value in {s.value for s in PaymentStatus}
                else None
            ),
        )
        service = payment_service()
        principal = self.principal()
        page = service.list_payments(principal, filters, limit=limit, offset=offset)
        return Response(
            {"count": page.total, "results": _payments_to_dicts(service, principal, page.items)}
        )

    @extend_schema(
        tags=_TAGS,
        operation_id="payments_register",
        summary="Register a payment",
        description=(
            "Records a received payment (or a refund) against a deal and/or a customer, as "
            "multipart/form-data with the `receipt` file (PDF/JPEG/PNG/WEBP/HEIC, at most "
            "10 MB; the type is detected from the file's content). Every payment needs its "
            "receipt (400 `receipt_required`); a refund may come without one (JSON is fine). "
            "A payment is created `pending_verification` and counts in sales, KPI, plans and "
            "reports only once confirmed (automatically, or by the head of sales / director: "
            "`POST /payments/{id}/confirm/`). A refund is confirmed when recorded. "
            "`close_sale=true` (recorded from the system stage «Продажа») wins the deal on "
            "confirmation. `kind` is prepayment/additional/full. A `reference` makes "
            "registration idempotent. `currency` defaults to the company currency; "
            "`seller_user_id` to the recorder."
        ),
        parameters=[_TENANT_HEADER],
        request={
            "multipart/form-data": PaymentRecordCreateSerializer,
            "application/json": PaymentRecordCreateSerializer,
        },
        responses={201: PaymentRecordReadSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = PaymentRecordCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        upload = data.get("receipt")
        receipt: ReceiptUpload | None = None
        if upload is not None:
            if upload.size is not None and upload.size > MAX_RECEIPT_BYTES:
                raise ReceiptRejectedError("The receipt must be at most 10 MB.")
            receipt = ReceiptUpload(content=upload.read(), filename=upload.name or "receipt")
        service = payment_service()
        principal = self.principal()
        payment = service.register(
            principal,
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
                close_sale=data["close_sale"],
            ),
            receipt,
        )
        return Response(_one(service, principal, payment), status=status.HTTP_201_CREATED)


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
        service = payment_service()
        principal = self.principal()
        return Response(_one(service, principal, service.get(principal, payment_id)))


class PaymentConfirmView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="payments_confirm",
        summary="Confirm a payment by its receipt",
        description=(
            "The head of sales (payments of their own department) or the director confirms "
            "a pending payment after checking its receipt; never the seller or the person "
            "who recorded it (403). From now on it counts in sales, KPI, plans and reports. "
            "A payment recorded from «Продажа» (`close_sale`) wins its open deal on the "
            "pipeline's «Продажа» stage. 409 `payment_already_verified` if already decided."
        ),
        parameters=[_TENANT_HEADER],
        request=None,
        responses=PaymentRecordReadSerializer,
    )
    def post(self, request: Request, payment_id: UUID) -> Response:
        service = payment_service()
        principal = self.principal()
        return Response(_one(service, principal, service.confirm(principal, payment_id)))


class PaymentRejectView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="payments_reject",
        summary="Reject a payment",
        description=(
            "Same people as confirm. The payment stays in the history with the `reason`, "
            "never counts, the deal keeps its stage, and the seller gets a notification."
        ),
        parameters=[_TENANT_HEADER],
        request=PaymentRejectSerializer,
        responses=PaymentRecordReadSerializer,
    )
    def post(self, request: Request, payment_id: UUID) -> Response:
        serializer = PaymentRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        service = payment_service()
        principal = self.principal()
        payment = service.reject(principal, payment_id, serializer.validated_data["reason"])
        return Response(_one(service, principal, payment))


class PaymentReceiptView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="payments_receipt",
        summary="Download the payment's receipt",
        description=(
            "Streams the uploaded receipt to whoever can see the payment. Shown inline "
            "(images, PDF); `?download=1` asks the browser to save it."
        ),
        parameters=[
            _TENANT_HEADER,
            OpenApiParameter("download", str, OpenApiParameter.QUERY, description="1 to save"),
        ],
        responses={(200, "application/octet-stream"): OpenApiTypes.BINARY},
    )
    def get(self, request: Request, payment_id: UUID) -> HttpResponse:
        file = payment_service().receipt(self.principal(), payment_id)
        response = HttpResponse(file.content, content_type=file.content_type)
        disposition = "attachment" if request.query_params.get("download") == "1" else "inline"
        # An ASCII fallback for old clients; the real (e.g. Cyrillic) name goes in filename*.
        stem, dot, ext = file.filename.rpartition(".")
        ascii_stem = (stem if dot else file.filename).encode("ascii", "ignore").decode().strip()
        ascii_ext = ext.encode("ascii", "ignore").decode() if dot else ""
        ascii_name = (ascii_stem or "receipt") + (f".{ascii_ext}" if ascii_ext else "")
        response["Content-Disposition"] = (
            f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(file.filename)}"
        )
        response["Content-Length"] = str(len(file.content))
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; sandbox"
        return response


class PaymentReviewQueueView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="payments_review_queue",
        summary="Payments waiting for verification",
        description=(
            "«Проверка оплат»: pending payments you can see, newest first, with the deal "
            "(title, amount), receipt metadata and `can_verify`. For the head of sales "
            "(their department) and the director; others get 403."
        ),
        parameters=[_TENANT_HEADER, _LIMIT, _OFFSET],
        responses=PaymentRecordListSerializer,
    )
    def get(self, request: Request) -> Response:
        try:
            limit = min(max(int(request.query_params.get("limit", 50)), 1), MAX_PAGE_SIZE)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except ValueError:
            limit, offset = 50, 0
        service = payment_service()
        principal = self.principal()
        page = service.review_queue(principal, limit=limit, offset=offset)
        return Response(
            {
                "count": page.total,
                "results": _payments_to_dicts(service, principal, page.items, with_deals=True),
            }
        )


class DealPaymentSummaryView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="payments_deal_summary",
        summary="Money of one deal",
        description=(
            "For the deal card: deal amount, confirmed received money (refunds subtracted), "
            "money waiting for verification, refunds and the client's debt (deal amount "
            "minus confirmed money), plus payment counts by status — in the deal's currency."
        ),
        parameters=[_TENANT_HEADER],
        responses=DealPaymentSummarySerializer,
    )
    def get(self, request: Request, deal_id: UUID) -> Response:
        summary = payment_service().deal_summary(self.principal(), deal_id)
        return Response(deal_summary_to_dict(summary))


class PaymentMetricsView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="payments_metrics",
        summary="Sales metrics for a period",
        description=(
            "Distinct figures for a period (default: the current month in the company "
            "timezone): sales_count (deals whose first payment fell in the period; each new "
            "deal is a sale), new_clients (clients whose first payment ever fell in it), "
            "turnover "
            "(their total value), received (money received, refunds subtracted) and debt "
            "(outstanding on those deals). Never collapsed into one number. "
            "`group_by=seller` adds `by_seller` (sales and new clients per seller) for any "
            "period: day, week or month via `paid_from`/`paid_to`. `department_id` narrows "
            "everything to one sales department (a head of sales: only their own, else 403). "
            "`prepayments_count`/`prepayments_amount`: prepayments paid in the period."
        ),
        parameters=[_TENANT_HEADER, _PAID_FROM, _PAID_TO, _DEPARTMENT],
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
            by_seller=request.query_params.get("group_by") == "seller",
            department_id=_uuid_param(request.query_params.get("department_id")),
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
