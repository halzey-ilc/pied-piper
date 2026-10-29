from django.urls import path

from modules.ai_insights.presentation.views import (
    AgentActionConfirmView,
    AgentActionRejectView,
    AgentRunDetailView,
    AgentRunsView,
    CallSummaryView,
    ConversationReplyView,
    DealBriefView,
    KpiExplanationView,
    OperationsDigestView,
    RiskReviewView,
)

urlpatterns = [
    path("calls/<uuid:call_id>/summary/", CallSummaryView.as_view(), name="ai-call-summary"),
    path(
        "conversations/<uuid:conversation_id>/reply/",
        ConversationReplyView.as_view(),
        name="ai-conversation-reply",
    ),
    path("deals/<uuid:deal_id>/brief/", DealBriefView.as_view(), name="ai-deal-brief"),
    path("agent/runs/", AgentRunsView.as_view(), name="ai-agent-runs"),
    path("agent/runs/<uuid:run_id>/", AgentRunDetailView.as_view(), name="ai-agent-run"),
    path(
        "agent/runs/<uuid:run_id>/actions/<int:index>/confirm/",
        AgentActionConfirmView.as_view(),
        name="ai-agent-action-confirm",
    ),
    path(
        "agent/runs/<uuid:run_id>/actions/<int:index>/reject/",
        AgentActionRejectView.as_view(),
        name="ai-agent-action-reject",
    ),
    path("risk/review/", RiskReviewView.as_view(), name="ai-risk-review"),
    path("operations/digest/", OperationsDigestView.as_view(), name="ai-operations-digest"),
    path("analytics/kpi-explanation/", KpiExplanationView.as_view(), name="ai-kpi-explanation"),
]
