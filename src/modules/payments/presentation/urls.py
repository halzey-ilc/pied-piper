from django.urls import path

from modules.payments.presentation.views import (
    InvoiceDetailView,
    InvoiceIssueView,
    InvoiceListCreateView,
    InvoicePaymentView,
    InvoiceVoidView,
)

urlpatterns = [
    path("invoices/", InvoiceListCreateView.as_view(), name="invoice-list"),
    path("invoices/<uuid:invoice_id>/", InvoiceDetailView.as_view(), name="invoice-detail"),
    path("invoices/<uuid:invoice_id>/issue/", InvoiceIssueView.as_view(), name="invoice-issue"),
    path(
        "invoices/<uuid:invoice_id>/payments/", InvoicePaymentView.as_view(), name="invoice-payment"
    ),
    path("invoices/<uuid:invoice_id>/void/", InvoiceVoidView.as_view(), name="invoice-void"),
]
