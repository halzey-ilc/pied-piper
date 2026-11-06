from __future__ import annotations

from typing import Any
from uuid import UUID

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.communications.composition import template_service, whatsapp_connected
from modules.communications.domain.conversation import Channel
from modules.communications.domain.template import (
    MAX_BODY_LENGTH,
    MAX_TITLE_LENGTH,
    MessageTemplate,
)
from modules.communications.presentation.views import _TENANT_HEADER, _Scoped

_TAGS = ["Communications"]


class TemplateWriteSerializer(serializers.Serializer[dict[str, Any]]):
    title = serializers.CharField(max_length=MAX_TITLE_LENGTH, required=False)
    body = serializers.CharField(max_length=MAX_BODY_LENGTH, required=False)


def template_to_dict(t: MessageTemplate) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "title": t.title,
        "body": t.body,
        "created_by": str(t.created_by),
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


class TemplateListView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="message_templates_list",
        summary="Message templates",
        description="The company's message templates, by title. Anyone who can see conversations.",
        parameters=[_TENANT_HEADER],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request) -> Response:
        items = template_service().list(self.principal())
        return Response({"count": len(items), "results": [template_to_dict(t) for t in items]})

    @extend_schema(
        tags=_TAGS,
        operation_id="message_templates_create",
        summary="Create a message template",
        description="Title and body are required. Head of sales or director. Audited.",
        parameters=[_TENANT_HEADER],
        request=TemplateWriteSerializer,
        responses={201: OpenApiTypes.OBJECT},
    )
    def post(self, request: Request) -> Response:
        s = TemplateWriteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        template = template_service().create(
            self.principal(),
            title=s.validated_data.get("title", ""),
            body=s.validated_data.get("body", ""),
        )
        return Response(template_to_dict(template), status=status.HTTP_201_CREATED)


class TemplateDetailView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="message_templates_update",
        summary="Edit a message template",
        parameters=[_TENANT_HEADER],
        request=TemplateWriteSerializer,
        responses=OpenApiTypes.OBJECT,
    )
    def patch(self, request: Request, template_id: UUID) -> Response:
        s = TemplateWriteSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        template = template_service().update(
            self.principal(),
            template_id,
            title=s.validated_data.get("title"),
            body=s.validated_data.get("body"),
        )
        return Response(template_to_dict(template))

    @extend_schema(
        tags=_TAGS,
        operation_id="message_templates_delete",
        summary="Delete a message template",
        parameters=[_TENANT_HEADER],
        request=None,
        responses={204: None},
    )
    def delete(self, request: Request, template_id: UUID) -> Response:
        template_service().delete(self.principal(), template_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ChannelsView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="communications_channels",
        summary="Can the CRM send through each channel",
        description=(
            "Per channel: `can_send` is true only when a real provider adapter is connected. "
            "Only WhatsApp has one, and only once its account is configured."
        ),
        parameters=[_TENANT_HEADER],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request) -> Response:
        connected = {Channel.WHATSAPP} if whatsapp_connected() else set()
        return Response(
            {
                "results": [
                    {
                        "channel": channel.value,
                        "can_send": channel in connected,
                        "reason": None if channel in connected else "provider_not_connected",
                    }
                    for channel in Channel
                ]
            }
        )
