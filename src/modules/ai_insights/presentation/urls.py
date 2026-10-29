from django.urls import path

from modules.ai_insights.presentation.views import (
    CallSummaryView,
    ConversationReplyView,
    DealBriefView,
    KpiExplanationView,
    OperationsDigestView,
)

urlpatterns = [
    path("calls/<uuid:call_id>/summary/", CallSummaryView.as_view(), name="ai-call-summary"),
    path(
        "conversations/<uuid:conversation_id>/reply/",
        ConversationReplyView.as_view(),
        name="ai-conversation-reply",
    ),
    path("deals/<uuid:deal_id>/brief/", DealBriefView.as_view(), name="ai-deal-brief"),
    path("operations/digest/", OperationsDigestView.as_view(), name="ai-operations-digest"),
    path("analytics/kpi-explanation/", KpiExplanationView.as_view(), name="ai-kpi-explanation"),
]
