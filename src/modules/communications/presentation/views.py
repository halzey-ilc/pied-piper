from __future__ import annotations

from uuid import UUID

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.communications.application.ports import ConversationFilter
from modules.communications.composition import conversation_service
from modules.communications.domain.conversation import Channel, ConversationStatus
from modules.communications.presentation.serializers import (
    ConversationListSerializer,
    ConversationReadSerializer,
    MessageListSerializer,
    MessageReadSerializer,
    SendMessageSerializer,
    conversation_to_dict,
    message_to_dict,
)
from platform_core.presentation.tenancy import TenantScopedAPIView

_TENANT_HEADER = OpenApiParameter(
    "X-Tenant",
    str,
    OpenApiParameter.HEADER,
    required=True,
    description="Short name (slug) of your company, e.g. `demo`.",
)
_CHANNEL = OpenApiParameter(
    "channel",
    str,
    OpenApiParameter.QUERY,
    description="Filter by channel.",
    enum=[c.value for c in Channel],
)
_STATUS = OpenApiParameter(
    "status",
    str,
    OpenApiParameter.QUERY,
    description="Filter by status.",
    enum=[s.value for s in ConversationStatus],
)
_LIMIT = OpenApiParameter(
    "limit", int, OpenApiParameter.QUERY, description="Page size 1-200 (default 50)."
)
_OFFSET = OpenApiParameter(
    "offset", int, OpenApiParameter.QUERY, description="Rows to skip (default 0)."
)

MAX_PAGE_SIZE = 200
_TAGS = ["Communications"]


class _Scoped(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
            department_id=self.membership.department_id,
        )

    def _paging(self, request: Request) -> tuple[int, int]:
        try:
            limit = min(max(int(request.query_params.get("limit", 50)), 1), MAX_PAGE_SIZE)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except ValueError:
            return 50, 0
        return limit, offset


class ConversationListView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="conversations_list",
        summary="List conversations",
        description=(
            "Conversations visible to you (own/team/all by role; unassigned ones are visible "
            "so they can be picked up), most recent activity first."
        ),
        parameters=[_TENANT_HEADER, _CHANNEL, _STATUS, _LIMIT, _OFFSET],
        responses=ConversationListSerializer,
    )
    def get(self, request: Request) -> Response:
        limit, offset = self._paging(request)
        channels = {c.value for c in Channel}
        statuses = {s.value for s in ConversationStatus}
        ch = request.query_params.get("channel")
        st = request.query_params.get("status")
        filters = ConversationFilter(
            channel=Channel(ch) if ch in channels else None,
            status=ConversationStatus(st) if st in statuses else None,
        )
        page = conversation_service().list_conversations(
            self.principal(), filters, limit=limit, offset=offset
        )
        return Response(
            {"count": page.total, "results": [conversation_to_dict(c) for c in page.items]}
        )


class ConversationDetailView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="conversations_retrieve",
        summary="Get a conversation",
        description="Returns a conversation by id, or 404 if not found or not visible to you.",
        parameters=[_TENANT_HEADER],
        responses=ConversationReadSerializer,
    )
    def get(self, request: Request, conversation_id: UUID) -> Response:
        return Response(
            conversation_to_dict(conversation_service().get(self.principal(), conversation_id))
        )


class MessagesView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="conversations_messages",
        summary="List messages",
        description="Messages in a conversation, oldest first.",
        parameters=[_TENANT_HEADER, _LIMIT, _OFFSET],
        responses=MessageListSerializer,
    )
    def get(self, request: Request, conversation_id: UUID) -> Response:
        limit, offset = self._paging(request)
        messages = conversation_service().messages(
            self.principal(), conversation_id, limit=limit, offset=offset
        )
        return Response({"results": [message_to_dict(m) for m in messages]})

    @extend_schema(
        tags=_TAGS,
        operation_id="conversations_send",
        summary="Send a message",
        description=(
            "Sends an outbound message in the conversation through its channel. In the "
            "scaffold the channel sender is a stub (no real message is delivered). Requires "
            "the communications.send permission."
        ),
        parameters=[_TENANT_HEADER],
        request=SendMessageSerializer,
        responses={201: MessageReadSerializer},
    )
    def post(self, request: Request, conversation_id: UUID) -> Response:
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = conversation_service().send_message(
            self.principal(), conversation_id, serializer.validated_data["text"]
        )
        return Response(message_to_dict(message), status=status.HTTP_201_CREATED)


class AssignView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="conversations_assign",
        summary="Assign to me",
        description="Takes ownership of the conversation. Requires communications.send.",
        parameters=[_TENANT_HEADER],
        request=None,
        responses=ConversationReadSerializer,
    )
    def post(self, request: Request, conversation_id: UUID) -> Response:
        return Response(
            conversation_to_dict(
                conversation_service().assign_to_me(self.principal(), conversation_id)
            )
        )


class ReadView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="conversations_mark_read",
        summary="Mark read",
        description="Clears the unread count on the conversation.",
        parameters=[_TENANT_HEADER],
        request=None,
        responses=ConversationReadSerializer,
    )
    def post(self, request: Request, conversation_id: UUID) -> Response:
        return Response(
            conversation_to_dict(
                conversation_service().mark_read(self.principal(), conversation_id)
            )
        )


class CloseView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="conversations_close",
        summary="Close a conversation",
        parameters=[_TENANT_HEADER],
        request=None,
        responses=ConversationReadSerializer,
    )
    def post(self, request: Request, conversation_id: UUID) -> Response:
        return Response(
            conversation_to_dict(conversation_service().close(self.principal(), conversation_id))
        )


class ReopenView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="conversations_reopen",
        summary="Reopen a conversation",
        parameters=[_TENANT_HEADER],
        request=None,
        responses=ConversationReadSerializer,
    )
    def post(self, request: Request, conversation_id: UUID) -> Response:
        return Response(
            conversation_to_dict(conversation_service().reopen(self.principal(), conversation_id))
        )
