"""Call analysis (transcript, review, score) and the company's call checklist."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.ai_insights.composition import call_analysis_service
from modules.ai_insights.domain.call_analysis import LANGUAGES, MAX_CRITERIA, CallAnalysis
from modules.ai_insights.presentation.views import _TAGS, _TENANT_HEADER, _Scoped


class AnalysisRequestSerializer(serializers.Serializer[dict[str, Any]]):
    language = serializers.ChoiceField(
        choices=list(LANGUAGES),
        required=False,
        help_text="Spoken language; default: the company's call language.",
    )


class ChecklistPointSerializer(serializers.Serializer[dict[str, Any]]):
    code = serializers.RegexField(r"^[a-z][a-z0-9_]{1,39}$")
    title = serializers.CharField(max_length=100)
    description = serializers.CharField(max_length=300, required=False, allow_blank=True)


class ChecklistSerializer(serializers.Serializer[dict[str, Any]]):
    criteria = serializers.ListField(
        child=ChecklistPointSerializer(), min_length=1, max_length=MAX_CRITERIA
    )


def analysis_to_dict(a: CallAnalysis) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "call_id": str(a.call_id),
        "status": a.status.value,
        "language": a.language,
        "segments": a.segments,
        "duration_s": a.duration_s,
        "review": a.review,
        "score": a.score,
        "is_mock": a.is_mock,
        "model": a.model,
        "error_code": a.error_code,
        # Kyrgyz speech models are not yet checked by a native speaker.
        "draft": a.language == "ky",
        "created_at": a.created_at.isoformat(),
        "finished_at": a.finished_at.isoformat() if a.finished_at else None,
    }


class CallAnalysisView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="ai_call_analysis_latest",
        summary="The call's transcript, review and score",
        description=(
            "Answered calls from a connected PBX are analysed automatically. `score` (0-100) "
            "is computed by the CRM from the verdicts; every positive verdict quotes the "
            "transcript. 404 if the call was not analysed or you cannot see it."
        ),
        parameters=[_TENANT_HEADER],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request, call_id: UUID) -> Response:
        return Response(analysis_to_dict(call_analysis_service().latest(self.principal(), call_id)))

    @extend_schema(
        tags=_TAGS,
        operation_id="ai_call_analysis_request",
        summary="Analyse the call again (e.g. in Kyrgyz)",
        description="Needs the call's recording from the PBX. Returns 202; poll GET.",
        parameters=[_TENANT_HEADER],
        request=AnalysisRequestSerializer,
        responses={202: OpenApiTypes.OBJECT},
    )
    def post(self, request: Request, call_id: UUID) -> Response:
        serializer = AnalysisRequestSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        analysis = call_analysis_service().request(
            self.principal(), call_id, language=serializer.validated_data.get("language")
        )
        return Response(analysis_to_dict(analysis), status=status.HTTP_202_ACCEPTED)


class CallChecklistView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="ai_call_checklist_get",
        summary="The checklist calls are reviewed against",
        parameters=[_TENANT_HEADER],
        responses=ChecklistSerializer,
    )
    def get(self, request: Request) -> Response:
        return Response({"criteria": call_analysis_service().checklist()})

    @extend_schema(
        tags=_TAGS,
        operation_id="ai_call_checklist_set",
        summary="Set the company's call checklist",
        description="1-15 points; applies to calls analysed from now on. company.settings.manage.",
        parameters=[_TENANT_HEADER],
        request=ChecklistSerializer,
        responses=ChecklistSerializer,
    )
    def put(self, request: Request) -> Response:
        serializer = ChecklistSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        criteria = call_analysis_service().set_checklist(
            self.principal(), [dict(c) for c in serializer.validated_data["criteria"]]
        )
        return Response({"criteria": criteria})
