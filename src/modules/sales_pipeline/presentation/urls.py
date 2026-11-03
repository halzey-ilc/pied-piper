from django.urls import path

from modules.sales_pipeline.presentation.views import (
    CustomFieldDetailView,
    CustomFieldListCreateView,
    DealAssignView,
    DealDetailView,
    DealHistoryView,
    DealListCreateView,
    DealLoseView,
    DealMoveView,
    DealReopenView,
    DealStageHistoryView,
    DealWinView,
    LossReasonDetailView,
    LossReasonListCreateView,
    PipelineAdoptDealsView,
    PipelineDetailView,
    PipelineListCreateView,
    PipelineStageDetailView,
    PipelineStageOrderView,
    PipelineStagesView,
)

urlpatterns = [
    path("pipelines/", PipelineListCreateView.as_view(), name="pipeline-list"),
    path("pipelines/<uuid:pipeline_id>/", PipelineDetailView.as_view(), name="pipeline-detail"),
    path(
        "pipelines/<uuid:pipeline_id>/stages/",
        PipelineStagesView.as_view(),
        name="pipeline-stages",
    ),
    path(
        "pipelines/<uuid:pipeline_id>/stages/<uuid:stage_id>/",
        PipelineStageDetailView.as_view(),
        name="pipeline-stage",
    ),
    path(
        "pipelines/<uuid:pipeline_id>/adopt-deals/",
        PipelineAdoptDealsView.as_view(),
        name="pipeline-adopt-deals",
    ),
    path(
        "pipelines/<uuid:pipeline_id>/stage-order/",
        PipelineStageOrderView.as_view(),
        name="pipeline-stage-order",
    ),
    path("loss-reasons/", LossReasonListCreateView.as_view(), name="loss-reason-list"),
    path(
        "loss-reasons/<uuid:loss_reason_id>/",
        LossReasonDetailView.as_view(),
        name="loss-reason-detail",
    ),
    path("custom-fields/", CustomFieldListCreateView.as_view(), name="custom-field-list"),
    path(
        "custom-fields/<uuid:field_id>/",
        CustomFieldDetailView.as_view(),
        name="custom-field-detail",
    ),
    path("deals/", DealListCreateView.as_view(), name="deal-list"),
    path("deals/<uuid:deal_id>/", DealDetailView.as_view(), name="deal-detail"),
    path("deals/<uuid:deal_id>/stage/", DealMoveView.as_view(), name="deal-move"),
    path(
        "deals/<uuid:deal_id>/stage-history/",
        DealStageHistoryView.as_view(),
        name="deal-stage-history",
    ),
    path("deals/<uuid:deal_id>/assign/", DealAssignView.as_view(), name="deal-assign"),
    path("deals/<uuid:deal_id>/history/", DealHistoryView.as_view(), name="deal-history"),
    path("deals/<uuid:deal_id>/win/", DealWinView.as_view(), name="deal-win"),
    path("deals/<uuid:deal_id>/lose/", DealLoseView.as_view(), name="deal-lose"),
    path("deals/<uuid:deal_id>/reopen/", DealReopenView.as_view(), name="deal-reopen"),
]
