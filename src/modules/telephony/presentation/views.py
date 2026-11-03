from __future__ import annotations

from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.organizations.composition import company_settings
from modules.telephony.application.ports import CallFilter
from modules.telephony.application.service import CallService, LogCall, RequestCall
from modules.telephony.composition import call_service
from modules.telephony.domain.call import CallDirection, CallLink, CallStatus, SubjectType
from modules.telephony.presentation.serializers import (
    CallAdvanceSerializer,
    CallEndSerializer,
    CallLinkSerializer,
    CallListSerializer,
    CallLogSerializer,
    CallReadSerializer,
    CallRequestSerializer,
    call_to_dict,
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
    enum=[s.value for s in CallStatus],
)
_DIRECTION = OpenApiParameter(
    "direction",
    str,
    OpenApiParameter.QUERY,
    description="Filter by direction.",
    enum=[d.value for d in CallDirection],
)
_STATUSES = OpenApiParameter(
    "statuses",
    str,
    OpenApiParameter.QUERY,
    description="Comma-separated statuses, any of them (e.g. `missed,failed`).",
)
_OWNER = OpenApiParameter(
    "owner_id", str, OpenApiParameter.QUERY, description="Calls of one member (the owner)."
)
_SUBJECT_TYPE = OpenApiParameter(
    "subject_type",
    str,
    OpenApiParameter.QUERY,
    description="With `subject_id`: calls linked to this customer or deal.",
    enum=[t.value for t in SubjectType],
)
_SUBJECT_ID = OpenApiParameter("subject_id", str, OpenApiParameter.QUERY)
_DATE_FROM = OpenApiParameter(
    "date_from", str, OpenApiParameter.QUERY, description="YYYY-MM-DD, company-local, inclusive."
)
_DATE_TO = OpenApiParameter(
    "date_to", str, OpenApiParameter.QUERY, description="YYYY-MM-DD, company-local, inclusive."
)
_SEARCH = OpenApiParameter(
    "search", str, OpenApiParameter.QUERY, description="Digits of a phone number or note text."
)
_LIMIT = OpenApiParameter(
    "limit", int, OpenApiParameter.QUERY, description="Page size 1-200 (default 50)."
)
_OFFSET = OpenApiParameter(
    "offset", int, OpenApiParameter.QUERY, description="Rows to skip (default 0)."
)

MAX_PAGE_SIZE = 200
_TAGS = ["Telephony"]


class _Scoped(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
            department_id=self.membership.department_id,
        )


def _link(data: dict[str, object]) -> CallLink | None:
    subject_type = data.get("subject_type")
    subject_id = data.get("subject_id")
    if not subject_type or not subject_id:
        return None
    return CallLink(type=SubjectType(subject_type), id=subject_id)  # type: ignore[arg-type]


class CallListCreateView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="calls_list",
        summary="List calls",
        description="Calls visible to you (own/team/all by role), newest first.",
        parameters=[
            _TENANT_HEADER,
            _STATUS,
            _STATUSES,
            _DIRECTION,
            _OWNER,
            _SUBJECT_TYPE,
            _SUBJECT_ID,
            _DATE_FROM,
            _DATE_TO,
            _SEARCH,
            _LIMIT,
            _OFFSET,
        ],
        responses=CallListSerializer,
    )
    def get(self, request: Request) -> Response:
        q = request.query_params
        try:
            limit = min(max(int(q.get("limit", 50)), 1), MAX_PAGE_SIZE)
            offset = max(int(q.get("offset", 0)), 0)
        except ValueError:
            limit, offset = 50, 0
        statuses = {s.value for s in CallStatus}
        directions = {d.value for d in CallDirection}
        subject_types = {t.value for t in SubjectType}
        s = q.get("status")
        d = q.get("direction")
        st = q.get("subject_type")
        many = {x.strip() for x in (q.get("statuses") or "").split(",")} & statuses
        try:
            zone = ZoneInfo(str(company_settings(self.tenant.tenant_id)["timezone"]))
            start = date.fromisoformat(q["date_from"]) if q.get("date_from") else None
            end = date.fromisoformat(q["date_to"]) if q.get("date_to") else None
            owner = UUID(q["owner_id"]) if q.get("owner_id") else None
            subject_id = UUID(q["subject_id"]) if q.get("subject_id") else None
        except ValueError as exc:
            raise DomainValidationError("Ids must be UUIDs and dates YYYY-MM-DD.") from exc
        filters = CallFilter(
            status=CallStatus(s) if s in statuses else None,
            direction=CallDirection(d) if d in directions else None,
            subject_type=SubjectType(st) if st in subject_types else None,
            subject_id=subject_id,
            statuses=frozenset(CallStatus(x) for x in many),
            owner_user_id=owner,
            started_from=datetime.combine(start, time.min, tzinfo=zone) if start else None,
            started_to=(
                datetime.combine(end + timedelta(days=1), time.min, tzinfo=zone) if end else None
            ),
            search=(q.get("search") or "").strip()[:100] or None,
        )
        page = call_service().list_calls(self.principal(), filters, limit=limit, offset=offset)
        return Response({"count": page.total, "results": [call_to_dict(c) for c in page.items]})

    @extend_schema(
        tags=_TAGS,
        operation_id="calls_log",
        summary="Log a call",
        description=(
            "Records a call that already happened (completed/missed/failed) with its numbers "
            "(E.164), start time and duration. A recording is referenced by URL only; no audio "
            "is stored. Optionally link a customer or deal."
        ),
        parameters=[_TENANT_HEADER],
        request=CallLogSerializer,
        responses={201: CallReadSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = CallLogSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        call = call_service().log_call(
            self.principal(),
            LogCall(
                direction=CallDirection(data["direction"]),
                from_number=data["from_number"],
                to_number=data["to_number"],
                status=CallStatus(data["status"]),
                started_at=data["started_at"],
                duration_seconds=data["duration_seconds"],
                recording_url=data.get("recording_url") or None,
                external_id=data.get("external_id") or None,
                notes=data.get("notes") or None,
                link=_link(data),
            ),
        )
        return Response(call_to_dict(call), status=status.HTTP_201_CREATED)


class CallRequestView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="calls_request",
        summary="Click-to-call (start an outbound call)",
        description=(
            "Starts an outbound call through the telephony provider and returns the call in "
            "`requested` status with the provider's id. In the scaffold the dialer is a stub "
            "(no real call is placed)."
        ),
        parameters=[_TENANT_HEADER],
        request=CallRequestSerializer,
        responses={201: CallReadSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = CallRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        call = call_service().request_call(
            self.principal(),
            RequestCall(
                from_number=data["from_number"], to_number=data["to_number"], link=_link(data)
            ),
        )
        return Response(call_to_dict(call), status=status.HTTP_201_CREATED)


class CallDetailView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="calls_retrieve",
        summary="Get one call",
        description="Returns a call by id, or 404 if not found or not visible to you.",
        parameters=[_TENANT_HEADER],
        responses=CallReadSerializer,
    )
    def get(self, request: Request, call_id: UUID) -> Response:
        return Response(call_to_dict(call_service().get(self.principal(), call_id)))

    @extend_schema(
        tags=_TAGS,
        operation_id="calls_link",
        summary="Link a call to a customer or deal",
        description=(
            "Attaches the call to a customer or deal (`subject_type` + `subject_id`), or "
            "detaches it with both null; optionally replaces the notes. Needs the right to "
            "update the call."
        ),
        parameters=[_TENANT_HEADER],
        request=CallLinkSerializer,
        responses=CallReadSerializer,
    )
    def patch(self, request: Request, call_id: UUID) -> Response:
        serializer = CallLinkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        service = call_service()
        principal = self.principal()
        # Without subject keys only the notes change; explicit nulls detach the call.
        touches_link = "subject_type" in request.data or "subject_id" in request.data
        link = _link(data) if touches_link else service.get(principal, call_id).link
        call = service.relink(principal, call_id, link, notes=data.get("notes"))
        return Response(call_to_dict(call))


class CallAdvanceView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="calls_advance",
        summary="Advance a call's status",
        description="Moves a click-to-call forward (ringing / in_progress). Not for ending a call.",
        parameters=[_TENANT_HEADER],
        request=CallAdvanceSerializer,
        responses=CallReadSerializer,
    )
    def post(self, request: Request, call_id: UUID) -> Response:
        serializer = CallAdvanceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        call = call_service().advance(
            self.principal(), call_id, CallStatus(serializer.validated_data["status"])
        )
        return Response(call_to_dict(call))


class CallEndView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="calls_end",
        summary="End a call",
        description="Ends an in-progress call (completed/missed/failed) with a duration and "
        "optional recording URL.",
        parameters=[_TENANT_HEADER],
        request=CallEndSerializer,
        responses=CallReadSerializer,
    )
    def post(self, request: Request, call_id: UUID) -> Response:
        serializer = CallEndSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        service: CallService = call_service()
        call = service.end(
            self.principal(),
            call_id,
            CallStatus(data["status"]),
            duration_seconds=data["duration_seconds"],
            recording_url=data.get("recording_url") or None,
        )
        return Response(call_to_dict(call))
