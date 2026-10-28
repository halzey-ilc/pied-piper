from __future__ import annotations

from uuid import UUID

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.ai_insights.composition import call_insight_service, operations_digest_service
from modules.ai_insights.presentation.serializers import (
    CallInsightSerializer,
    DigestRequestSerializer,
    OperationsDigestSerializer,
    digest_to_dict,
    insight_to_dict,
)
from platform_core.presentation.tenancy import TenantScopedAPIView

_TENANT_HEADER = OpenApiParameter(
    "X-Tenant",
    str,
    OpenApiParameter.HEADER,
    required=True,
    description="Short name (slug) of your company, e.g. `demo`.",
    examples=[OpenApiExample("Demo tenant", value="demo")],
)
_TAGS = ["AI insights"]


class _Scoped(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
        )


class CallSummaryView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="ai_call_summary_request",
        summary="Request an AI summary of a call",
        description=(
            "Queues an AI summary of the call's notes and returns immediately (202). A worker "
            "sends the text to the AI Platform and stores the structured result; poll GET. "
            "Asking again for an unchanged call returns the existing summary (200). Requires "
            "the ai.request permission on the call. 409 `ai_disabled` when AI is switched off."
        ),
        parameters=[_TENANT_HEADER],
        request=None,
        responses={202: CallInsightSerializer, 200: CallInsightSerializer},
    )
    def post(self, request: Request, call_id: UUID) -> Response:
        insight, created = call_insight_service().request_call_summary(self.principal(), call_id)
        return Response(
            insight_to_dict(insight),
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )

    @extend_schema(
        tags=_TAGS,
        operation_id="ai_call_summary_latest",
        summary="Latest AI summary of a call",
        description="The newest summary of a call you can see, or 404 if none was requested.",
        parameters=[_TENANT_HEADER],
        responses=CallInsightSerializer,
    )
    def get(self, request: Request, call_id: UUID) -> Response:
        insight = call_insight_service().latest_call_summary(self.principal(), call_id)
        return Response(insight_to_dict(insight))


class OperationsDigestView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="ai_operations_digest_request",
        summary="Request an AI explanation of your operations findings",
        description=(
            "Takes the findings of the CRM's operations rules over the records you can see "
            "(ids, kinds and ages only), asks the AI Platform to explain them and returns "
            "immediately (202). Poll GET. An unchanged set of findings returns the existing "
            "digest (200). Requires the ai.request permission. 409 `ai_disabled` when AI is off."
        ),
        parameters=[_TENANT_HEADER],
        request=DigestRequestSerializer,
        responses={202: OperationsDigestSerializer, 200: OperationsDigestSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = DigestRequestSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        digest, created = operations_digest_service().request(
            self.principal(), language=serializer.validated_data.get("language")
        )
        return Response(
            digest_to_dict(digest),
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )

    @extend_schema(
        tags=_TAGS,
        operation_id="ai_operations_digest_latest",
        summary="Your latest operations digest",
        description="Your own newest digest (manual or daily), or 404. Never another user's.",
        parameters=[_TENANT_HEADER],
        responses=OperationsDigestSerializer,
    )
    def get(self, request: Request) -> Response:
        return Response(digest_to_dict(operations_digest_service().latest(self.principal())))
