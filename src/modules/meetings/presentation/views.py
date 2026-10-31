from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.meetings.application.ports import MeetingFilter
from modules.meetings.application.service import EditMeeting, MeetingService, ScheduleMeeting
from modules.meetings.composition import meeting_service
from modules.meetings.domain.meeting import (
    MeetingLink,
    MeetingStatus,
    Participant,
    ParticipantResponse,
    SubjectType,
)
from modules.meetings.presentation.serializers import (
    MeetingCancelSerializer,
    MeetingCompleteSerializer,
    MeetingCreateSerializer,
    MeetingListSerializer,
    MeetingReadSerializer,
    MeetingRescheduleSerializer,
    MeetingUpdateSerializer,
    ParticipantInputSerializer,
    ParticipantResponseSerializer,
    meeting_to_dict,
)
from platform_core.presentation.tenancy import TenantScopedAPIView

_TENANT_HEADER = OpenApiParameter(
    "X-Tenant",
    str,
    OpenApiParameter.HEADER,
    required=True,
    description="Short name (slug) of your company, e.g. `demo`. Get it from GET /api/v1/me/.",
    examples=[OpenApiExample("Demo tenant", value="demo")],
)
_STATUS = OpenApiParameter(
    "status",
    str,
    OpenApiParameter.QUERY,
    description="Filter by status.",
    enum=[s.value for s in MeetingStatus],
)
_SUBJECT_TYPE = OpenApiParameter(
    "subject_type",
    str,
    OpenApiParameter.QUERY,
    description="Filter by linked subject type.",
    enum=[s.value for s in SubjectType],
)
_SUBJECT_ID = OpenApiParameter(
    "subject_id",
    str,
    OpenApiParameter.QUERY,
    description="Filter by linked subject id (with subject_type).",
)
_UPCOMING = OpenApiParameter(
    "upcoming",
    bool,
    OpenApiParameter.QUERY,
    description="If true, only meetings starting now or later.",
)
_LIMIT = OpenApiParameter(
    "limit", int, OpenApiParameter.QUERY, description="Page size 1-200 (default 50)."
)
_OFFSET = OpenApiParameter(
    "offset", int, OpenApiParameter.QUERY, description="Rows to skip (default 0)."
)

MAX_PAGE_SIZE = 200
_TAGS = ["Meetings"]


class _Scoped(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
            department_id=self.membership.department_id,
        )


def _link(data: dict[str, Any]) -> MeetingLink | None:
    subject_type = data.get("subject_type")
    subject_id = data.get("subject_id")
    if not subject_type or not subject_id:
        return None
    return MeetingLink(type=SubjectType(subject_type), id=subject_id)


def _participants(raw: list[dict[str, Any]]) -> tuple[Participant, ...]:
    return tuple(
        Participant.create(name=p["name"], email=p.get("email") or None, user_id=p.get("user_id"))
        for p in raw
    )


class MeetingListCreateView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="meetings_list",
        summary="List meetings",
        description=(
            "Returns meetings visible to you (own / team / all by role), earliest first. "
            "Filter by status, linked subject, or `upcoming=true`; page with limit/offset."
        ),
        parameters=[
            _TENANT_HEADER,
            _STATUS,
            _SUBJECT_TYPE,
            _SUBJECT_ID,
            _UPCOMING,
            _LIMIT,
            _OFFSET,
        ],
        responses=MeetingListSerializer,
    )
    def get(self, request: Request) -> Response:
        try:
            limit = min(max(int(request.query_params.get("limit", 50)), 1), MAX_PAGE_SIZE)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except ValueError:
            limit, offset = 50, 0
        status_value = request.query_params.get("status")
        valid = {s.value for s in MeetingStatus}
        subject_type = request.query_params.get("subject_type")
        subject_id = _as_uuid(request.query_params.get("subject_id"))
        upcoming = request.query_params.get("upcoming") == "true"
        filters = MeetingFilter(
            status=MeetingStatus(status_value) if status_value in valid else None,
            subject_type=SubjectType(subject_type)
            if subject_type in {s.value for s in SubjectType}
            else None,
            subject_id=subject_id,
            starts_after=datetime.now(UTC) if upcoming else None,
        )
        page = meeting_service().list_meetings(
            self.principal(), filters, limit=limit, offset=offset
        )
        return Response({"count": page.total, "results": [meeting_to_dict(m) for m in page.items]})

    @extend_schema(
        tags=_TAGS,
        operation_id="meetings_create",
        summary="Schedule a meeting",
        description=(
            "Schedules a meeting (status `scheduled`). `scheduled_end` must be after "
            "`scheduled_start` and within 24 hours. Optionally link a customer or deal, add "
            "participants (name required, e-mail optional) and reminder offsets in minutes "
            "before the start."
        ),
        parameters=[_TENANT_HEADER],
        request=MeetingCreateSerializer,
        responses={201: MeetingReadSerializer},
        examples=[
            OpenApiExample(
                "Meeting with a customer",
                value={
                    "title": "Kickoff call",
                    "scheduled_start": "2026-11-05T10:00:00Z",
                    "scheduled_end": "2026-11-05T10:30:00Z",
                    "subject_type": "customer",
                    "subject_id": "00000000-0000-0000-0000-000000000000",
                    "participants": [{"name": "Jane Buyer", "email": "jane@example.test"}],
                    "reminders": [60, 10],
                },
                request_only=True,
            )
        ],
    )
    def post(self, request: Request) -> Response:
        serializer = MeetingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        meeting = meeting_service().schedule(
            self.principal(),
            ScheduleMeeting(
                title=data["title"],
                scheduled_start=data["scheduled_start"],
                scheduled_end=data["scheduled_end"],
                description=data.get("description") or None,
                location=data.get("location") or None,
                link=_link(data),
                participants=_participants(data.get("participants", [])),
                reminders=tuple(data.get("reminders", [])),
            ),
        )
        return Response(meeting_to_dict(meeting), status=status.HTTP_201_CREATED)


class MeetingDetailView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="meetings_retrieve",
        summary="Get one meeting",
        description=(
            "Returns a meeting by id, or 404 if it does not exist or is not visible to you."
        ),
        parameters=[_TENANT_HEADER],
        responses=MeetingReadSerializer,
    )
    def get(self, request: Request, meeting_id: UUID) -> Response:
        return Response(meeting_to_dict(meeting_service().get(self.principal(), meeting_id)))

    @extend_schema(
        tags=_TAGS,
        operation_id="meetings_update",
        summary="Edit a meeting",
        description=(
            "Edits title, description, location or reminders of a scheduled meeting. Send only "
            "changed fields. Optional `version` gives optimistic locking (409 on mismatch)."
        ),
        parameters=[_TENANT_HEADER],
        request=MeetingUpdateSerializer,
        responses=MeetingReadSerializer,
    )
    def patch(self, request: Request, meeting_id: UUID) -> Response:
        serializer = MeetingUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        reminders = data.get("reminders")
        meeting = meeting_service().edit(
            self.principal(),
            EditMeeting(
                meeting_id=meeting_id,
                expected_version=data.get("version"),
                title=data.get("title"),
                description=data.get("description"),
                location=data.get("location"),
                set_description="description" in request.data,
                set_location="location" in request.data,
                reminders=tuple(reminders) if reminders is not None else None,
            ),
        )
        return Response(meeting_to_dict(meeting))


def _service(view: _Scoped) -> tuple[MeetingService, Principal]:
    return meeting_service(), view.principal()


class MeetingRescheduleView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="meetings_reschedule",
        summary="Reschedule a meeting",
        description="Moves a scheduled meeting to new start/end times.",
        parameters=[_TENANT_HEADER],
        request=MeetingRescheduleSerializer,
        responses=MeetingReadSerializer,
    )
    def post(self, request: Request, meeting_id: UUID) -> Response:
        serializer = MeetingRescheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        service, principal = _service(self)
        meeting = service.reschedule(
            principal,
            meeting_id,
            serializer.validated_data["scheduled_start"],
            serializer.validated_data["scheduled_end"],
        )
        return Response(meeting_to_dict(meeting))


class MeetingCompleteView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="meetings_complete",
        summary="Mark a meeting completed",
        description="Marks a scheduled meeting as completed, optionally with an outcome note.",
        parameters=[_TENANT_HEADER],
        request=MeetingCompleteSerializer,
        responses=MeetingReadSerializer,
    )
    def post(self, request: Request, meeting_id: UUID) -> Response:
        serializer = MeetingCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        service, principal = _service(self)
        meeting = service.complete(
            principal, meeting_id, serializer.validated_data.get("outcome") or None
        )
        return Response(meeting_to_dict(meeting))


class MeetingCancelView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="meetings_cancel",
        summary="Cancel a meeting",
        description="Cancels a scheduled meeting with a required reason.",
        parameters=[_TENANT_HEADER],
        request=MeetingCancelSerializer,
        responses=MeetingReadSerializer,
    )
    def post(self, request: Request, meeting_id: UUID) -> Response:
        serializer = MeetingCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        service, principal = _service(self)
        meeting = service.cancel(principal, meeting_id, serializer.validated_data["reason"])
        return Response(meeting_to_dict(meeting))


class MeetingParticipantsView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="meetings_add_participant",
        summary="Add a participant",
        description="Adds a participant to a scheduled meeting (deduplicated by e-mail or name).",
        parameters=[_TENANT_HEADER],
        request=ParticipantInputSerializer,
        responses=MeetingReadSerializer,
    )
    def post(self, request: Request, meeting_id: UUID) -> Response:
        serializer = ParticipantInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        service, principal = _service(self)
        participant = Participant.create(
            name=data["name"], email=data.get("email") or None, user_id=data.get("user_id")
        )
        meeting = service.add_participant(principal, meeting_id, participant)
        return Response(meeting_to_dict(meeting))


class MeetingRespondView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="meetings_participant_respond",
        summary="Set a participant response",
        description="Sets a participant's response (needs_action / accepted / declined). "
        "`key` is the participant's e-mail if present, otherwise their name.",
        parameters=[_TENANT_HEADER],
        request=ParticipantResponseSerializer,
        responses=MeetingReadSerializer,
    )
    def post(self, request: Request, meeting_id: UUID) -> Response:
        serializer = ParticipantResponseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        service, principal = _service(self)
        meeting = service.set_response(
            principal,
            meeting_id,
            serializer.validated_data["key"],
            ParticipantResponse(serializer.validated_data["response"]),
        )
        return Response(meeting_to_dict(meeting))


def _as_uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None
