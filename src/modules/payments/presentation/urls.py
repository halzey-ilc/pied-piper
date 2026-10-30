from django.urls import path

from modules.payments.presentation.views import (
    InvoiceDetailView,
    InvoiceIssueView,
    InvoiceListCreateView,
    InvoicePaymentView,
    InvoiceVoidView,
    PaymentDetailView,
    PaymentListCreateView,
    PaymentMetricsView,
)

urlpatterns = [
    path("", PaymentListCreateView.as_view(), name="payment-list"),
    path("metrics/", PaymentMetricsView.as_view(), name="payment-metrics"),
    path("<uuid:payment_id>/", PaymentDetailView.as_view(), name="payment-detail"),
    path("invoices/", InvoiceListCreateView.as_view(), name="invoice-list"),
    path("invoices/<uuid:invoice_id>/", InvoiceDetailView.as_view(), name="invoice-detail"),
    path("invoices/<uuid:invoice_id>/issue/", InvoiceIssueView.as_view(), name="invoice-issue"),
    path(
        "invoices/<uuid:invoice_id>/payments/", InvoicePaymentView.as_view(), name="invoice-payment"
    ),
    path("invoices/<uuid:invoice_id>/void/", InvoiceVoidView.as_view(), name="invoice-void"),
]
