from __future__ import annotations

from uuid import UUID

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.ai_insights.composition import (
    agent_service,
    ai_report_service,
    call_insight_service,
    deal_brief_service,
    reply_suggestion_service,
)
from modules.ai_insights.domain.report import ReportKind
from modules.ai_insights.presentation.serializers import (
    AgentActionSerializer,
    AgentRequestSerializer,
    AgentRunSerializer,
    AiReportSerializer,
    CallInsightSerializer,
    DealBriefSerializer,
    DigestRequestSerializer,
    KpiExplanationRequestSerializer,
    ReplyRequestSerializer,
    ReplySuggestionSerializer,
    action_to_dict,
    brief_to_dict,
    insight_to_dict,
    reply_to_dict,
    report_to_dict,
    run_to_dict,
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
            department_id=self.membership.department_id,
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


def _accepted(created: bool) -> int:
    return status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK


class OperationsDigestView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="ai_operations_digest_request",
        summary="Request an AI explanation of your operations findings",
        description=(
            "Takes the findings of the CRM's operations rules over the records you can see "
            "(ids, kinds and ages only), asks the AI Platform to explain them and returns "
            "immediately (202). Poll GET. An unchanged set of findings returns the existing "
            "report (200). Requires ai.request. 409 `ai_disabled` when AI is off."
        ),
        parameters=[_TENANT_HEADER],
        request=DigestRequestSerializer,
        responses={202: AiReportSerializer, 200: AiReportSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = DigestRequestSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        report, created = ai_report_service().request_digest(
            self.principal(), language=serializer.validated_data.get("language")
        )
        return Response(report_to_dict(report), status=_accepted(created))

    @extend_schema(
        tags=_TAGS,
        operation_id="ai_operations_digest_latest",
        summary="Your latest operations digest",
        description="Your own newest digest (manual or daily), or 404. Never another user's.",
        parameters=[_TENANT_HEADER],
        responses=AiReportSerializer,
    )
    def get(self, request: Request) -> Response:
        report = ai_report_service().latest(self.principal(), ReportKind.OPERATIONS_DIGEST)
        return Response(report_to_dict(report))


class ConversationReplyView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="ai_conversation_reply_request",
        summary="Request an AI reply suggestion for a conversation (Communication Assistant)",
        description=(
            "Classifies the customer's latest message (intent, urgency, with a verbatim quote "
            "as evidence) and drafts a reply. The latest messages are sent to the AI Platform "
            "without the contact's phone number or name. **Nothing is sent to the customer**: "
            "send your own message with the conversation's send endpoint. Returns 202 at once; "
            "poll GET. An unchanged conversation returns the existing suggestion (200). 400 "
            "when the last message is the company's. Requires ai.request on the conversation "
            "(assign an unassigned conversation first)."
        ),
        parameters=[_TENANT_HEADER],
        request=ReplyRequestSerializer,
        responses={202: ReplySuggestionSerializer, 200: ReplySuggestionSerializer},
    )
    def post(self, request: Request, conversation_id: UUID) -> Response:
        serializer = ReplyRequestSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        suggestion, created = reply_suggestion_service().request_reply(
            self.principal(), conversation_id, language=serializer.validated_data.get("language")
        )
        return Response(reply_to_dict(suggestion), status=_accepted(created))

    @extend_schema(
        tags=_TAGS,
        operation_id="ai_conversation_reply_latest",
        summary="Latest AI reply suggestion for a conversation",
        parameters=[_TENANT_HEADER],
        responses=ReplySuggestionSerializer,
    )
    def get(self, request: Request, conversation_id: UUID) -> Response:
        suggestion = reply_suggestion_service().latest(self.principal(), conversation_id)
        return Response(reply_to_dict(suggestion))


class DealBriefView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="ai_deal_brief_request",
        summary="Request an AI brief for the next conversation about a deal (Sales Coach)",
        description=(
            "Collects the deal (stage, amount, dates) and its notes, tasks, meetings and calls "
            "that you can see, computes overdue tasks and the deal's age, and asks the AI "
            "Platform for the situation, risks, questions to clarify and a next step. Every "
            "point cites the records it is based on. Phone numbers are not sent. Returns 202 "
            "at once; poll GET. Unchanged data returns your existing brief (200). Briefs are "
            "personal. Requires ai.request on the deal."
        ),
        parameters=[_TENANT_HEADER],
        request=ReplyRequestSerializer,
        responses={202: DealBriefSerializer, 200: DealBriefSerializer},
    )
    def post(self, request: Request, deal_id: UUID) -> Response:
        serializer = ReplyRequestSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        brief, created = deal_brief_service().request_brief(
            self.principal(), deal_id, language=serializer.validated_data.get("language")
        )
        return Response(brief_to_dict(brief), status=_accepted(created))

    @extend_schema(
        tags=_TAGS,
        operation_id="ai_deal_brief_latest",
        summary="Your latest AI brief for a deal",
        parameters=[_TENANT_HEADER],
        responses=DealBriefSerializer,
    )
    def get(self, request: Request, deal_id: UUID) -> Response:
        brief = deal_brief_service().latest(self.principal(), deal_id)
        return Response(brief_to_dict(brief))


class RiskReviewView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="ai_risk_review_request",
        summary="Request a neutral AI explanation of risk signals (Risk & Anomaly)",
        description=(
            "Sends the company's current risk signals (refs, kinds, counts and thresholds "
            "only) to the AI Platform, which explains them and suggests what to check. Texts "
            "that state or imply wrongdoing are rejected: a signal is never evidence "
            "(`is_evidence` is false). Returns 202; poll GET. Requires risk.view and ai.request "
            "(owner, admin)."
        ),
        parameters=[_TENANT_HEADER],
        request=DigestRequestSerializer,
        responses={202: AiReportSerializer, 200: AiReportSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = DigestRequestSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        report, created = ai_report_service().request_risk_review(
            self.principal(), language=serializer.validated_data.get("language")
        )
        return Response(report_to_dict(report), status=_accepted(created))

    @extend_schema(
        tags=_TAGS,
        operation_id="ai_risk_review_latest",
        summary="Your latest risk review",
        parameters=[_TENANT_HEADER],
        responses=AiReportSerializer,
    )
    def get(self, request: Request) -> Response:
        report = ai_report_service().latest(self.principal(), ReportKind.RISK_REVIEW)
        return Response(report_to_dict(report))


class AgentRunsView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="ai_agent_run_request",
        summary="Ask the CRM assistant to plan actions (Agent Runtime)",
        description=(
            "The AI Platform receives your request, the tools you may use (create_task, "
            "log_note, request_deal_brief) and the records you name (deal title; a customer is "
            "referred to by kind only). It proposes actions; **nothing runs** until you confirm "
            "each one. Returns 202; poll GET. Requires ai.request."
        ),
        parameters=[_TENANT_HEADER],
        request=AgentRequestSerializer,
        responses={202: AgentRunSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = AgentRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        run = agent_service().request_run(
            self.principal(),
            data["request"],
            deal_id=data.get("deal_id"),
            customer_id=data.get("customer_id"),
            language=data.get("language"),
        )
        return Response(run_to_dict(run), status=status.HTTP_202_ACCEPTED)


class AgentRunDetailView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="ai_agent_run_get",
        summary="An assistant run and its proposed actions",
        description="Only the person who made the request can see it.",
        parameters=[_TENANT_HEADER],
        responses=AgentRunSerializer,
    )
    def get(self, request: Request, run_id: UUID) -> Response:
        return Response(run_to_dict(agent_service().get(self.principal(), run_id)))


class AgentActionConfirmView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="ai_agent_action_confirm",
        summary="Confirm and run one proposed action",
        description=(
            "Runs the action through the owning module with your own rights (e.g. the "
            "activities module creates the task, checking your permissions). Confirming again "
            "returns the earlier outcome; the action never runs twice. Audited."
        ),
        parameters=[_TENANT_HEADER],
        request=None,
        responses=AgentActionSerializer,
    )
    def post(self, request: Request, run_id: UUID, index: int) -> Response:
        action = agent_service().confirm(self.principal(), run_id, index)
        return Response(action_to_dict(action))


class AgentActionRejectView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="ai_agent_action_reject",
        summary="Reject one proposed action",
        parameters=[_TENANT_HEADER],
        request=None,
        responses=AgentActionSerializer,
    )
    def post(self, request: Request, run_id: UUID, index: int) -> Response:
        action = agent_service().reject(self.principal(), run_id, index)
        return Response(action_to_dict(action))


class KpiExplanationView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="ai_kpi_explanation_request",
        summary="Request an AI explanation of company KPIs (AI Business Analyst)",
        description=(
            "The CRM computes the KPIs of the period containing `date` and of the period "
            "before it, and the changes between them (SQL and code, never the AI). The AI "
            "Platform explains these numbers; any text with a number not in `input` is "
            "rejected. Returns 202 at once; poll GET. Unchanged numbers return the existing "
            "report (200). Requires reports.view and ai.request."
        ),
        parameters=[_TENANT_HEADER],
        request=KpiExplanationRequestSerializer,
        responses={202: AiReportSerializer, 200: AiReportSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = KpiExplanationRequestSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        report, created = ai_report_service().request_kpi_explanation(
            self.principal(),
            period=data["period"],
            day=data.get("date"),
            language=data.get("language"),
        )
        return Response(report_to_dict(report), status=_accepted(created))

    @extend_schema(
        tags=_TAGS,
        operation_id="ai_kpi_explanation_latest",
        summary="Your latest KPI explanation",
        parameters=[_TENANT_HEADER],
        responses=AiReportSerializer,
    )
    def get(self, request: Request) -> Response:
        report = ai_report_service().latest(self.principal(), ReportKind.KPI_EXPLANATION)
        return Response(report_to_dict(report))
