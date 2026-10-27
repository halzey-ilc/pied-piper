from django.urls import path

from modules.sales_pipeline.presentation.views import (
    DealDetailView,
    DealListCreateView,
    DealLoseView,
    DealMoveView,
    DealReopenView,
    DealWinView,
    PipelineDetailView,
    PipelineListCreateView,
)

urlpatterns = [
    path("pipelines/", PipelineListCreateView.as_view(), name="pipeline-list"),
    path("pipelines/<uuid:pipeline_id>/", PipelineDetailView.as_view(), name="pipeline-detail"),
    path("deals/", DealListCreateView.as_view(), name="deal-list"),
    path("deals/<uuid:deal_id>/", DealDetailView.as_view(), name="deal-detail"),
    path("deals/<uuid:deal_id>/stage/", DealMoveView.as_view(), name="deal-move"),
    path("deals/<uuid:deal_id>/win/", DealWinView.as_view(), name="deal-win"),
    path("deals/<uuid:deal_id>/lose/", DealLoseView.as_view(), name="deal-lose"),
    path("deals/<uuid:deal_id>/reopen/", DealReopenView.as_view(), name="deal-reopen"),
]
