from django.urls import path

from modules.sales_pipeline.presentation.views import (
    DealDetailView,
    DealListCreateView,
    DealLoseView,
    DealMoveView,
    DealReopenView,
    DealWinView,
    LossReasonDetailView,
    LossReasonListCreateView,
    PipelineDetailView,
    PipelineListCreateView,
)

urlpatterns = [
    path("pipelines/", PipelineListCreateView.as_view(), name="pipeline-list"),
    path("pipelines/<uuid:pipeline_id>/", PipelineDetailView.as_view(), name="pipeline-detail"),
    path("loss-reasons/", LossReasonListCreateView.as_view(), name="loss-reason-list"),
    path(
        "loss-reasons/<uuid:loss_reason_id>/",
        LossReasonDetailView.as_view(),
        name="loss-reason-detail",
    ),
    path("deals/", DealListCreateView.as_view(), name="deal-list"),
    path("deals/<uuid:deal_id>/", DealDetailView.as_view(), name="deal-detail"),
    path("deals/<uuid:deal_id>/stage/", DealMoveView.as_view(), name="deal-move"),
    path("deals/<uuid:deal_id>/win/", DealWinView.as_view(), name="deal-win"),
    path("deals/<uuid:deal_id>/lose/", DealLoseView.as_view(), name="deal-lose"),
    path("deals/<uuid:deal_id>/reopen/", DealReopenView.as_view(), name="deal-reopen"),
]
