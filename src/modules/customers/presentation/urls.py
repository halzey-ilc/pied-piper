from django.urls import path

from modules.customers.presentation.views import CustomerDetailView, CustomerListCreateView

urlpatterns = [
    path("", CustomerListCreateView.as_view(), name="customer-list"),
    path("<uuid:customer_id>/", CustomerDetailView.as_view(), name="customer-detail"),
]
