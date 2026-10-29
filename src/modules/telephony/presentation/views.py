from __future__ import annotations

from uuid import UUID

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.telephony.application.ports import CallFilter
from modules.telephony.application.service import CallService, LogCall, RequestCall
from modules.telephony.composition import call_service
from modules.telephony.domain.call import CallDirection, CallLink, CallStatus, SubjectType
from modules.telephony.presentation.serializers import (
    CallAdvanceSerializer,
    CallEndSerializer,
    CallListSerializer,
    CallLogSerializer,
    CallReadSerializer,
    CallRequestSerializer,
    call_to_dict,
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
    enum=[s.value for s in CallStatus],
)
_DIRECTION = OpenApiParameter(
    "direction",
    str,
    OpenApiParameter.QUERY,
    description="Filter by direction.",
    enum=[d.value for d in CallDirection],
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
        parameters=[_TENANT_HEADER, _STATUS, _DIRECTION, _LIMIT, _OFFSET],
        responses=CallListSerializer,
    )
    def get(self, request: Request) -> Response:
        try:
            limit = min(max(int(request.query_params.get("limit", 50)), 1), MAX_PAGE_SIZE)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except ValueError:
            limit, offset = 50, 0
        statuses = {s.value for s in CallStatus}
        directions = {d.value for d in CallDirection}
        s = request.query_params.get("status")
        d = request.query_params.get("direction")
        filters = CallFilter(
            status=CallStatus(s) if s in statuses else None,
            direction=CallDirection(d) if d in directions else None,
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
