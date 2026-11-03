from __future__ import annotations

from typing import Any
from uuid import UUID

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.notifications.application.ports import NotificationFilter
from modules.notifications.composition import notification_service
from modules.notifications.domain.notification import Notification, NotificationCategory
from modules.notifications.domain.preferences import (
    MAX_REMINDER_DAYS_BEFORE,
    NotificationPreferences,
)
from platform_core.presentation.tenancy import TenantScopedAPIView

_TENANT_HEADER = OpenApiParameter(
    "X-Tenant",
    str,
    OpenApiParameter.HEADER,
    required=True,
    description="Short name (slug) of your company, e.g. `demo`.",
)
_UNREAD = OpenApiParameter(
    "unread", bool, OpenApiParameter.QUERY, description="Only unread when true."
)
_CATEGORY = OpenApiParameter(
    "category",
    str,
    OpenApiParameter.QUERY,
    description="Filter by category.",
    enum=[c.value for c in NotificationCategory],
)
_LIMIT = OpenApiParameter(
    "limit", int, OpenApiParameter.QUERY, description="Page size 1-200 (default 50)."
)
_OFFSET = OpenApiParameter(
    "offset", int, OpenApiParameter.QUERY, description="Rows to skip (default 0)."
)

MAX_PAGE_SIZE = 200
_TAGS = ["Notifications"]


class PreferenceUpdateSerializer(serializers.Serializer[dict[str, Any]]):
    """Either a category toggle (``category`` + ``enabled``) or the task reminder lead time."""

    category = serializers.ChoiceField(
        choices=[c.value for c in NotificationCategory], required=False
    )
    enabled = serializers.BooleanField(required=False)
    reminder_days_before = serializers.IntegerField(
        required=False, min_value=0, max_value=MAX_REMINDER_DAYS_BEFORE
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        toggle = "category" in attrs or "enabled" in attrs
        if toggle and not ("category" in attrs and "enabled" in attrs):
            raise serializers.ValidationError("Pass both category and enabled.")
        if not toggle and "reminder_days_before" not in attrs:
            raise serializers.ValidationError("Pass category and enabled, or reminder_days_before.")
        return attrs


class _Scoped(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
            department_id=self.membership.department_id,
        )


def _to_dict(n: Notification) -> dict[str, Any]:
    return {
        "id": str(n.id),
        "category": n.category.value,
        "title": n.title,
        "body": n.body,
        "link": n.link,
        "read": n.read,
        "read_at": n.read_at.isoformat() if n.read_at else None,
        "created_at": n.created_at.isoformat(),
    }


def _prefs_dict(prefs: NotificationPreferences) -> dict[str, Any]:
    return {
        "categories": {c.value: prefs.is_enabled(c) for c in NotificationCategory},
        "reminder_days_before": prefs.reminder_days_before,
        "version": prefs.version,
    }


class NotificationListView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="notifications_list",
        summary="List my notifications",
        description="Your own notifications, newest first, with the unread count.",
        parameters=[_TENANT_HEADER, _UNREAD, _CATEGORY, _LIMIT, _OFFSET],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request) -> Response:
        try:
            limit = min(max(int(request.query_params.get("limit", 50)), 1), MAX_PAGE_SIZE)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except ValueError:
            limit, offset = 50, 0
        category = request.query_params.get("category")
        valid = {c.value for c in NotificationCategory}
        filters = NotificationFilter(
            unread_only=request.query_params.get("unread") == "true",
            category=NotificationCategory(category) if category in valid else None,
        )
        page = notification_service().list_for(
            self.principal().user_id, filters, limit=limit, offset=offset
        )
        return Response(
            {
                "count": page.total,
                "unread": page.unread,
                "results": [_to_dict(n) for n in page.items],
            }
        )


class UnreadCountView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="notifications_unread_count",
        summary="Unread count",
        description="Number of unread notifications for the signed-in user.",
        parameters=[_TENANT_HEADER],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request) -> Response:
        return Response({"unread": notification_service().unread_count(self.principal().user_id)})


class MarkReadView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="notifications_mark_read",
        summary="Mark one as read",
        description="Marks one of your notifications as read.",
        parameters=[_TENANT_HEADER],
        request=None,
        responses=OpenApiTypes.OBJECT,
    )
    def post(self, request: Request, notification_id: UUID) -> Response:
        notification = notification_service().mark_read(self.principal().user_id, notification_id)
        return Response(_to_dict(notification))


class MarkAllReadView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="notifications_mark_all_read",
        summary="Mark all as read",
        description="Marks all of your unread notifications as read; returns how many changed.",
        parameters=[_TENANT_HEADER],
        request=None,
        responses=OpenApiTypes.OBJECT,
    )
    def post(self, request: Request) -> Response:
        return Response({"marked": notification_service().mark_all_read(self.principal().user_id)})


class PreferencesView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="notifications_preferences",
        summary="Get notification preferences",
        description="Which categories you receive in-app (all on by default).",
        parameters=[_TENANT_HEADER],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request) -> Response:
        return Response(
            _prefs_dict(notification_service().get_preferences(self.principal().user_id))
        )

    @extend_schema(
        tags=_TAGS,
        operation_id="notifications_set_preference",
        summary="Enable/disable a category or set the task reminder lead time",
        description=(
            "Turns a notification category on or off for you, and/or sets "
            "`reminder_days_before` (0-7): how many days before a task's due date the "
            "reminder comes (0 = on the due day). Overdue alerts come the day after the due "
            "date; the head of sales is told when a task is 2 calendar days overdue."
        ),
        parameters=[_TENANT_HEADER],
        request=PreferenceUpdateSerializer,
        responses=OpenApiTypes.OBJECT,
    )
    def post(self, request: Request) -> Response:
        serializer = PreferenceUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        service = notification_service()
        user_id = self.principal().user_id
        prefs = service.get_preferences(user_id)
        if "category" in data:
            prefs = service.set_preference(
                user_id, NotificationCategory(data["category"]), enabled=data["enabled"]
            )
        if "reminder_days_before" in data:
            prefs = service.set_reminder_days(user_id, data["reminder_days_before"])
        return Response(_prefs_dict(prefs))
