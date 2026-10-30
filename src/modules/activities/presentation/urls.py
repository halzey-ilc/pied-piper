from django.urls import path

from modules.activities.presentation.views import (
    ActivityCancelView,
    ActivityCompleteView,
    ActivityDetailView,
    ActivityListCreateView,
    ActivityReassignView,
    ActivityReopenView,
)

urlpatterns = [
    path("", ActivityListCreateView.as_view(), name="activity-list"),
    path("<uuid:activity_id>/", ActivityDetailView.as_view(), name="activity-detail"),
    path("<uuid:activity_id>/complete/", ActivityCompleteView.as_view(), name="activity-complete"),
    path("<uuid:activity_id>/cancel/", ActivityCancelView.as_view(), name="activity-cancel"),
    path("<uuid:activity_id>/reassign/", ActivityReassignView.as_view(), name="activity-reassign"),
    path("<uuid:activity_id>/reopen/", ActivityReopenView.as_view(), name="activity-reopen"),
]
