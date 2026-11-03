from django.urls import path

from modules.customers.presentation.merge_views import CustomerMergeView
from modules.customers.presentation.views import (
    CustomerArchiveView,
    CustomerAssignView,
    CustomerDetailView,
    CustomerHistoryView,
    CustomerListCreateView,
    CustomerRestoreView,
)

urlpatterns = [
    path("", CustomerListCreateView.as_view(), name="customer-list"),
    path("<uuid:customer_id>/", CustomerDetailView.as_view(), name="customer-detail"),
    path("<uuid:customer_id>/archive/", CustomerArchiveView.as_view(), name="customer-archive"),
    path("<uuid:customer_id>/restore/", CustomerRestoreView.as_view(), name="customer-restore"),
    path("<uuid:customer_id>/assign/", CustomerAssignView.as_view(), name="customer-assign"),
    path("<uuid:customer_id>/history/", CustomerHistoryView.as_view(), name="customer-history"),
    path("<uuid:customer_id>/merge/", CustomerMergeView.as_view(), name="customer-merge"),
]
