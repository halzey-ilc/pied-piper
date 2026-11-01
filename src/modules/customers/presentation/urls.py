from django.urls import path

from modules.customers.presentation.views import (
    CustomerArchiveView,
    CustomerAssignView,
    CustomerDetailView,
    CustomerListCreateView,
    CustomerRestoreView,
)

urlpatterns = [
    path("", CustomerListCreateView.as_view(), name="customer-list"),
    path("<uuid:customer_id>/", CustomerDetailView.as_view(), name="customer-detail"),
    path("<uuid:customer_id>/archive/", CustomerArchiveView.as_view(), name="customer-archive"),
    path("<uuid:customer_id>/restore/", CustomerRestoreView.as_view(), name="customer-restore"),
    path("<uuid:customer_id>/assign/", CustomerAssignView.as_view(), name="customer-assign"),
]
