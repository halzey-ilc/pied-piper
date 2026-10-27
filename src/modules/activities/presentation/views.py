from __future__ import annotations

from typing import Any
from uuid import UUID

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.activities.application.ports import ActivityFilter
from modules.activities.application.service import (
    ActivityService,
    EditActivity,
    LogCall,
    LogNote,
    LogTask,
)
from modules.activities.composition import activity_service
from modules.activities.domain.activity import (
    ActivityKind,
    CallDirection,
    SubjectRef,
    SubjectType,
    TaskStatus,
)
from modules.activities.presentation.serializers import (
    ActivityCreateSerializer,
    ActivityListSerializer,
    ActivityReadSerializer,
    ActivityUpdateSerializer,
    activity_to_dict,
)
from platform_core.presentation.tenancy import TenantScopedAPIView
from shared.domain.errors import DomainValidationError

_TENANT_HEADER = OpenApiParameter(
    "X-Tenant",
    str,
    OpenApiParameter.HEADER,
    required=True,
    description="Short name (slug) of your company, e.g. `demo`. Get it from GET /api/v1/me/.",
    examples=[OpenApiExample("Demo tenant", value="demo")],
)
_KIND = OpenApiParameter(
    "kind",
    str,
    OpenApiParameter.QUERY,
    description="Filter by kind.",
    enum=[k.value for k in ActivityKind],
)
_SUBJECT_TYPE = OpenApiParameter(
    "subject_type",
    str,
    OpenApiParameter.QUERY,
    description="Timeline: subject type.",
    enum=[s.value for s in SubjectType],
)
_SUBJECT_ID = OpenApiParameter(
    "subject_id",
    str,
    OpenApiParameter.QUERY,
    description="Timeline: subject id (use with subject_type).",
)
_TASK_STATUS = OpenApiParameter(
    "task_status",
    str,
    OpenApiParameter.QUERY,
    description="Filter tasks by status.",
    enum=[s.value for s in TaskStatus],
)
_LIMIT = OpenApiParameter(
    "limit", int, OpenApiParameter.QUERY, description="Page size 1-200 (default 50)."
)
_OFFSET = OpenApiParameter(
    "offset", int, OpenApiParameter.QUERY, description="Rows to skip (default 0)."
)

MAX_PAGE_SIZE = 200
_TAGS = ["Activities"]


class _Scoped(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
        )


def _subject(data: dict[str, Any]) -> SubjectRef:
    return SubjectRef(type=SubjectType(data["subject_type"]), id=data["subject_id"])


class ActivityListCreateView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="activities_timeline",
        summary="List activities / timeline",
        description=(
            "Returns activities visible to you (own / team / all by role), newest first. "
            "Pass `subject_type` and `subject_id` together to get the timeline of one "
            "customer or deal. Filter by `kind` and `task_status`; page with limit/offset."
        ),
        parameters=[
            _TENANT_HEADER,
            _KIND,
            _SUBJECT_TYPE,
            _SUBJECT_ID,
            _TASK_STATUS,
            _LIMIT,
            _OFFSET,
        ],
        responses=ActivityListSerializer,
    )
    def get(self, request: Request) -> Response:
        try:
            limit = min(max(int(request.query_params.get("limit", 50)), 1), MAX_PAGE_SIZE)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except ValueError:
            limit, offset = 50, 0
        filters = ActivityFilter(
            kind=_enum(ActivityKind, request.query_params.get("kind")),
            subject=_subject_filter(request),
            task_status=_enum(TaskStatus, request.query_params.get("task_status")),
        )
        page = activity_service().timeline(self.principal(), filters, limit=limit, offset=offset)
        return Response({"count": page.total, "results": [activity_to_dict(a) for a in page.items]})

    @extend_schema(
        tags=_TAGS,
        operation_id="activities_create",
        summary="Log an activity",
        description=(
            "Logs a task, call or note against a customer or deal. The subject must exist in "
            "this company. Required fields depend on `kind`: task needs `title`; call needs "
            "`direction` and `occurred_at`; note needs `body`."
        ),
        parameters=[_TENANT_HEADER],
        request=ActivityCreateSerializer,
        responses={201: ActivityReadSerializer},
        examples=[
            OpenApiExample(
                "Task on a customer",
                value={
                    "kind": "task",
                    "subject_type": "customer",
                    "subject_id": "00000000-0000-0000-0000-000000000000",
                    "title": "Call back next week",
                    "due_date": "2026-11-05",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Note on a deal",
                value={
                    "kind": "note",
                    "subject_type": "deal",
                    "subject_id": "00000000-0000-0000-0000-000000000000",
                    "body": "Budget confirmed by the CFO.",
                },
                request_only=True,
            ),
        ],
    )
    def post(self, request: Request) -> Response:
        serializer = ActivityCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        activity = self._dispatch_create(serializer.validated_data)
        return Response(activity_to_dict(activity), status=status.HTTP_201_CREATED)

    def _dispatch_create(self, data: dict[str, Any]) -> Any:
        service: ActivityService = activity_service()
        principal = self.principal()
        subject = _subject(data)
        kind = ActivityKind(data["kind"])
        if kind is ActivityKind.TASK:
            return service.log_task(
                principal,
                LogTask(
                    subject=subject,
                    title=data.get("title") or "",
                    body=data.get("body") or None,
                    due_date=data.get("due_date"),
                ),
            )
        if kind is ActivityKind.CALL:
            if not data.get("direction") or not data.get("occurred_at"):
                raise DomainValidationError("A call needs a direction and occurred_at.")
            return service.log_call(
                principal,
                LogCall(
                    subject=subject,
                    direction=CallDirection(data["direction"]),
                    occurred_at=data["occurred_at"],
                    duration_seconds=data.get("duration_seconds", 0),
                    outcome=data.get("outcome") or None,
                    body=data.get("body") or None,
                ),
            )
        return service.log_note(
            principal,
            LogNote(subject=subject, body=data.get("body") or "", title=data.get("title") or None),
        )


class ActivityDetailView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="activities_retrieve",
        summary="Get one activity",
        description=(
            "Returns an activity by id, or 404 if it does not exist or is not visible to you."
        ),
        parameters=[_TENANT_HEADER],
        responses=ActivityReadSerializer,
    )
    def get(self, request: Request, activity_id: UUID) -> Response:
        return Response(activity_to_dict(activity_service().get(self.principal(), activity_id)))

    @extend_schema(
        tags=_TAGS,
        operation_id="activities_update",
        summary="Edit an activity",
        description=(
            "Edits the title, body and (for tasks) the due date. Send only changed fields; "
            "pass `due_date` as null to clear it. Optional `version` gives optimistic locking."
        ),
        parameters=[_TENANT_HEADER],
        request=ActivityUpdateSerializer,
        responses=ActivityReadSerializer,
    )
    def patch(self, request: Request, activity_id: UUID) -> Response:
        serializer = ActivityUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        activity = activity_service().edit(
            self.principal(),
            EditActivity(
                activity_id=activity_id,
                expected_version=data.get("version"),
                title=data.get("title"),
                body=data.get("body"),
                due_date=data.get("due_date"),
                set_due_date="due_date" in request.data,
            ),
        )
        return Response(activity_to_dict(activity))


class ActivityCompleteView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="activities_complete",
        summary="Complete a task",
        description="Marks a task activity as completed. Only tasks can be completed.",
        parameters=[_TENANT_HEADER],
        request=None,
        responses=ActivityReadSerializer,
    )
    def post(self, request: Request, activity_id: UUID) -> Response:
        return Response(
            activity_to_dict(activity_service().complete_task(self.principal(), activity_id))
        )


class ActivityReopenView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="activities_reopen",
        summary="Reopen a task",
        description="Reopens a completed task.",
        parameters=[_TENANT_HEADER],
        request=None,
        responses=ActivityReadSerializer,
    )
    def post(self, request: Request, activity_id: UUID) -> Response:
        return Response(
            activity_to_dict(activity_service().reopen_task(self.principal(), activity_id))
        )


def _enum(enum_cls: Any, value: str | None) -> Any:
    if value and value in {m.value for m in enum_cls}:
        return enum_cls(value)
    return None


def _subject_filter(request: Request) -> SubjectRef | None:
    subject_type = _enum(SubjectType, request.query_params.get("subject_type"))
    subject_id = request.query_params.get("subject_id")
    if subject_type is None or not subject_id:
        return None
    try:
        return SubjectRef(type=subject_type, id=UUID(subject_id))
    except ValueError:
        return None
