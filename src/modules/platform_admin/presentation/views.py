"""Platform administrator API (``/api/v1/platform/``) and the company side of support
requests (``/api/v1/support/``).

Platform endpoints take no X-Tenant: they act on the registry of all companies and are
open only to an account with ``is_platform_admin`` — checked here and again in every
use case. A company role, the director's included, never opens them.
"""

from __future__ import annotations

from datetime import date
from typing import Any, NoReturn, cast
from uuid import UUID

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.platform_admin.application.ports import CompanyFilter, NewDirector, PlatformActor
from modules.platform_admin.application.service import NewCompany, RecordPayment, Terms
from modules.platform_admin.composition import platform_admin_service, support_service
from modules.platform_admin.domain.support import ErrorStatus, TicketStatus
from modules.platform_admin.presentation.serializers import (
    CompanyCreateSerializer,
    CompanyUpdateSerializer,
    ErrorStatusSerializer,
    PaymentCancelSerializer,
    PaymentRecordSerializer,
    TermsSerializer,
    TicketCreateSerializer,
    TicketUpdateSerializer,
    catalog_to_dict,
    detail_to_dict,
    error_to_dict,
    overview_to_dict,
    payment_to_dict,
    summary_to_dict,
    ticket_to_dict,
)
from modules.subscriptions.application.ports import PaymentFilter
from modules.subscriptions.domain.payment import PaymentStatus
from modules.subscriptions.domain.plan import Feature, PlanCode
from modules.subscriptions.domain.subscription import AccessState, SubscriptionStatus
from platform_core.presentation.tenancy import TenantScopedAPIView
from shared.domain.errors import AccessDeniedError, DomainValidationError

_TAGS = ["Platform administration"]
_MAX_LIMIT = 100


class IsPlatformAdmin(BasePermission):
    message = "Only the platform administrator can do this."

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = request.user
        return bool(
            user is not None
            and user.is_authenticated
            and getattr(user, "is_platform_admin", False)
            and not getattr(user, "must_change_password", False)
        )


class _Platform(APIView):
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def permission_denied(
        self, request: Request, message: str | None = None, code: str | None = None
    ) -> NoReturn:
        if request.user is not None and request.user.is_authenticated:
            raise AccessDeniedError(message or IsPlatformAdmin.message)
        super().permission_denied(request, message, code)

    def actor(self) -> PlatformActor:
        user = cast(Any, self.request.user)
        return PlatformActor(user_id=user.pk, is_platform_admin=bool(user.is_platform_admin))


def _page_params(request: Request) -> tuple[int, int]:
    try:
        limit = int(request.query_params.get("limit", 25))
        offset = int(request.query_params.get("offset", 0))
    except ValueError as exc:
        raise DomainValidationError("limit and offset must be integers.") from exc
    if not 1 <= limit <= _MAX_LIMIT or offset < 0:
        raise DomainValidationError(f"limit must be 1-{_MAX_LIMIT}, offset non-negative.")
    return limit, offset


def _enum(value: str | None, enum: Any, name: str) -> Any:
    if value in (None, ""):
        return None
    try:
        return enum(value)
    except ValueError as exc:
        raise DomainValidationError(f"Unknown {name}.") from exc


def _terms(data: dict[str, Any]) -> Terms:
    features = data.get("features")
    return Terms(
        plan_code=PlanCode(data["plan_code"]),
        starts_on=data["starts_on"],
        ends_on=data.get("ends_on"),
        features=None if features is None else frozenset(Feature(f) for f in features),
        status=SubscriptionStatus(data.get("status") or "active"),
    )


class PlatformOverviewView(_Platform):
    @extend_schema(
        operation_id="platform_overview",
        tags=_TAGS,
        summary="Platform overview",
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request) -> Response:
        try:
            days = int(request.query_params.get("period_days", 30))
        except ValueError as exc:
            raise DomainValidationError("period_days must be an integer.") from exc
        return Response(overview_to_dict(platform_admin_service().overview(self.actor(), days)))


class PlatformCatalogView(_Platform):
    @extend_schema(
        operation_id="platform_catalog",
        tags=_TAGS,
        summary="Plans and modules",
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request) -> Response:
        return Response(catalog_to_dict())


class PlatformCompaniesView(_Platform):
    @extend_schema(
        operation_id="platform_companies_list",
        tags=_TAGS,
        summary="Companies",
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request) -> Response:
        limit, offset = _page_params(request)
        q = request.query_params
        active = q.get("status")
        filters = CompanyFilter(
            search=(q.get("search") or "").strip() or None,
            is_active={"active": True, "suspended": False}.get(active or ""),
            plan_code=_enum(q.get("plan"), PlanCode, "plan"),
            access=_enum(q.get("access"), AccessState, "access"),
        )
        items, total = platform_admin_service().companies(
            self.actor(), filters, limit=limit, offset=offset
        )
        return Response({"count": total, "results": [summary_to_dict(c) for c in items]})

    @extend_schema(
        operation_id="platform_companies_create",
        tags=_TAGS,
        summary="Connect a company",
        description=(
            "Creates the company, its database, the first director and the subscription in "
            "one operation. The director's temporary password is returned once."
        ),
        request=CompanyCreateSerializer,
        responses=OpenApiTypes.OBJECT,
    )
    def post(self, request: Request) -> Response:
        s = CompanyCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        director = d["director"]
        created = platform_admin_service().create_company(
            self.actor(),
            NewCompany(
                name=d["name"],
                slug=d["slug"],
                country=d["country"],
                currency=d["currency"],
                timezone=d["timezone"],
                terms=_terms(d["subscription"]),
                director=NewDirector(
                    first_name=director["first_name"],
                    last_name=director.get("last_name", ""),
                    login=director.get("login") or None,
                    email=director.get("email") or None,
                    phone=director.get("phone", ""),
                ),
            ),
        )
        return Response(
            {
                "company": detail_to_dict(created.company),
                "director": {
                    "login": created.director_login,
                    "temporary_password": created.temporary_password,
                },
            },
            status=status.HTTP_201_CREATED,
        )


class PlatformCompanyDetailView(_Platform):
    @extend_schema(
        operation_id="platform_companies_retrieve",
        tags=_TAGS,
        summary="Company card",
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request, company_id: UUID) -> Response:
        return Response(detail_to_dict(platform_admin_service().company(self.actor(), company_id)))

    @extend_schema(
        operation_id="platform_companies_update",
        tags=_TAGS,
        summary="Rename, suspend or resume a company",
        request=CompanyUpdateSerializer,
        responses=OpenApiTypes.OBJECT,
    )
    def patch(self, request: Request, company_id: UUID) -> Response:
        s = CompanyUpdateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        detail = platform_admin_service().update_company(
            self.actor(), company_id, **s.validated_data
        )
        return Response(detail_to_dict(detail))


class PlatformDirectorCredentialsView(_Platform):
    @extend_schema(
        operation_id="platform_director_credentials_issue",
        tags=_TAGS,
        summary="Issue new temporary credentials to a director",
        description=(
            "For a forgotten password or lost initial credentials. Generates a new temporary "
            "password (returned once, never retrievable later); the old password, sessions "
            "and reset links stop working; a password change is required at sign-in. 404 "
            "if the user is not an active director of this company."
        ),
        request=None,
        responses=OpenApiTypes.OBJECT,
    )
    def post(self, request: Request, company_id: UUID, user_id: UUID) -> Response:
        login, password = platform_admin_service().issue_director_credentials(
            self.actor(), company_id, user_id
        )
        return Response({"login": login, "temporary_password": password})


class PlatformCompanySubscriptionView(_Platform):
    @extend_schema(
        operation_id="platform_companies_subscription_set",
        tags=_TAGS,
        summary="Assign plan, term and modules",
        description="An assignment by the platform, not a payment record.",
        request=TermsSerializer,
        responses=OpenApiTypes.OBJECT,
    )
    def put(self, request: Request, company_id: UUID) -> Response:
        s = TermsSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        detail = platform_admin_service().set_subscription(
            self.actor(), company_id, _terms(s.validated_data)
        )
        return Response(detail_to_dict(detail))


class PlatformCompanyPaymentsView(_Platform):
    @extend_schema(
        operation_id="platform_company_payments_list",
        tags=_TAGS,
        summary="Subscription payments of a company",
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request, company_id: UUID) -> Response:
        limit, offset = _page_params(request)
        service = platform_admin_service()
        service.company(self.actor(), company_id)  # 404 for an unknown company
        items, total = service.payments(
            self.actor(), PaymentFilter(organization_id=company_id), limit=limit, offset=offset
        )
        return Response({"count": total, "results": [payment_to_dict(p) for p in items]})

    @extend_schema(
        operation_id="platform_company_payments_create",
        tags=_TAGS,
        summary="Record a subscription payment",
        description=(
            "Manual entry of money the platform received (no payment provider is "
            "connected). `reference` is the external operation id and is unique: recording "
            "it twice returns 409 `duplicate_payment`. A pending payment is not revenue "
            "until confirmed; `confirm: true` records and confirms at once."
        ),
        request=PaymentRecordSerializer,
        responses=OpenApiTypes.OBJECT,
    )
    def post(self, request: Request, company_id: UUID) -> Response:
        s = PaymentRecordSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        payment = platform_admin_service().record_payment(
            self.actor(),
            company_id,
            RecordPayment(
                amount=d["amount"],
                currency=d["currency"],
                paid_on=d["paid_on"],
                reference=d["reference"],
                period_start=d.get("period_start"),
                period_end=d.get("period_end"),
                note=d.get("note", ""),
                confirm=d.get("confirm", False),
            ),
        )
        return Response(payment_to_dict(payment), status=status.HTTP_201_CREATED)


class PlatformPaymentsView(_Platform):
    @extend_schema(
        operation_id="platform_payments_list",
        tags=_TAGS,
        summary="Subscription payments of all companies",
        description="Filter with ?status=pending|confirmed|cancelled&paid_from&paid_to&company_id.",
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request) -> Response:
        limit, offset = _page_params(request)
        q = request.query_params
        try:
            filters = PaymentFilter(
                organization_id=UUID(q["company_id"]) if q.get("company_id") else None,
                status=_enum(q.get("status"), PaymentStatus, "status"),
                paid_from=date.fromisoformat(q["paid_from"]) if q.get("paid_from") else None,
                paid_to=date.fromisoformat(q["paid_to"]) if q.get("paid_to") else None,
            )
        except ValueError as exc:
            raise DomainValidationError("company_id must be a UUID, dates YYYY-MM-DD.") from exc
        items, total = platform_admin_service().payments(
            self.actor(), filters, limit=limit, offset=offset
        )
        return Response({"count": total, "results": [payment_to_dict(p) for p in items]})


class PlatformPaymentConfirmView(_Platform):
    @extend_schema(
        operation_id="platform_payments_confirm",
        tags=_TAGS,
        summary="Confirm a pending payment",
        request=None,
        responses=OpenApiTypes.OBJECT,
    )
    def post(self, request: Request, payment_id: UUID) -> Response:
        payment = platform_admin_service().confirm_payment(self.actor(), payment_id)
        return Response(payment_to_dict(payment))


class PlatformPaymentCancelView(_Platform):
    @extend_schema(
        operation_id="platform_payments_cancel",
        tags=_TAGS,
        summary="Cancel a pending payment (recorded by mistake / never arrived)",
        request=PaymentCancelSerializer,
        responses=OpenApiTypes.OBJECT,
    )
    def post(self, request: Request, payment_id: UUID) -> Response:
        s = PaymentCancelSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        payment = platform_admin_service().cancel_payment(
            self.actor(), payment_id, s.validated_data["note"]
        )
        return Response(payment_to_dict(payment))


class PlatformErrorsView(_Platform):
    @extend_schema(
        operation_id="platform_errors_list",
        tags=_TAGS,
        summary="Server errors",
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request) -> Response:
        limit, offset = _page_params(request)
        items, total = platform_admin_service().errors(
            self.actor(),
            _enum(request.query_params.get("status"), ErrorStatus, "status"),
            limit=limit,
            offset=offset,
        )
        return Response({"count": total, "results": [error_to_dict(e) for e in items]})


class PlatformErrorDetailView(_Platform):
    @extend_schema(
        operation_id="platform_errors_update",
        tags=_TAGS,
        summary="Mark an error acknowledged or resolved",
        request=ErrorStatusSerializer,
        responses=OpenApiTypes.OBJECT,
    )
    def patch(self, request: Request, error_id: UUID) -> Response:
        s = ErrorStatusSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        record = platform_admin_service().set_error_status(
            self.actor(), error_id, ErrorStatus(s.validated_data["status"])
        )
        return Response(error_to_dict(record))


class PlatformTicketsView(_Platform):
    @extend_schema(
        operation_id="platform_tickets_list",
        tags=_TAGS,
        summary="Support requests",
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request) -> Response:
        limit, offset = _page_params(request)
        items, total = platform_admin_service().tickets(
            self.actor(),
            _enum(request.query_params.get("status"), TicketStatus, "status"),
            limit=limit,
            offset=offset,
        )
        return Response(
            {"count": total, "results": [ticket_to_dict(t, internal=True) for t in items]}
        )


class PlatformTicketDetailView(_Platform):
    @extend_schema(
        operation_id="platform_tickets_retrieve",
        tags=_TAGS,
        summary="Support request",
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request, ticket_id: UUID) -> Response:
        ticket = platform_admin_service().ticket(self.actor(), ticket_id)
        return Response(ticket_to_dict(ticket, internal=True))

    @extend_schema(
        operation_id="platform_tickets_update",
        tags=_TAGS,
        summary="Change status or internal note",
        request=TicketUpdateSerializer,
        responses=OpenApiTypes.OBJECT,
    )
    def patch(self, request: Request, ticket_id: UUID) -> Response:
        s = TicketUpdateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        ticket = platform_admin_service().update_ticket(
            self.actor(),
            ticket_id,
            status=TicketStatus(d["status"]) if "status" in d else None,
            internal_note=d.get("internal_note"),
        )
        return Response(ticket_to_dict(ticket, internal=True))


class SupportTicketsView(TenantScopedAPIView):
    """A company member sends a request to the platform and follows their own requests."""

    @extend_schema(
        operation_id="support_tickets_list",
        tags=["Support"],
        summary="My support requests",
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request) -> Response:
        limit, offset = _page_params(request)
        items, total = support_service().mine(
            self.membership.tenant_id, self.membership.user_id, limit=limit, offset=offset
        )
        return Response(
            {"count": total, "results": [ticket_to_dict(t, internal=False) for t in items]}
        )

    @extend_schema(
        operation_id="support_tickets_create",
        tags=["Support"],
        summary="Send a request to the platform",
        request=TicketCreateSerializer,
        responses=OpenApiTypes.OBJECT,
    )
    def post(self, request: Request) -> Response:
        s = TicketCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        ticket = support_service().send(
            self.membership.tenant_id, self.membership.user_id, d["kind"], d["subject"], d["body"]
        )
        return Response(ticket_to_dict(ticket, internal=False), status=status.HTTP_201_CREATED)
