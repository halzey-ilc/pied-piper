from django.urls import path

from modules.ai_insights.presentation.views import CallSummaryView, OperationsDigestView

urlpatterns = [
    path("calls/<uuid:call_id>/summary/", CallSummaryView.as_view(), name="ai-call-summary"),
    path("operations/digest/", OperationsDigestView.as_view(), name="ai-operations-digest"),
]
