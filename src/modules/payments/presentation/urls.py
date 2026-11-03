from django.urls import path

from modules.payments.presentation.views import (
    DealPaymentSummaryView,
    InvoiceDetailView,
    InvoiceIssueView,
    InvoiceListCreateView,
    InvoicePaymentView,
    InvoiceVoidView,
    PaymentConfirmView,
    PaymentDetailView,
    PaymentListCreateView,
    PaymentMetricsView,
    PaymentReceiptView,
    PaymentRejectView,
    PaymentReviewQueueView,
)

urlpatterns = [
    path("", PaymentListCreateView.as_view(), name="payment-list"),
    path("metrics/", PaymentMetricsView.as_view(), name="payment-metrics"),
    path("review/", PaymentReviewQueueView.as_view(), name="payment-review"),
    path(
        "deals/<uuid:deal_id>/summary/",
        DealPaymentSummaryView.as_view(),
        name="payment-deal-summary",
    ),
    path("<uuid:payment_id>/", PaymentDetailView.as_view(), name="payment-detail"),
    path("<uuid:payment_id>/confirm/", PaymentConfirmView.as_view(), name="payment-confirm"),
    path("<uuid:payment_id>/reject/", PaymentRejectView.as_view(), name="payment-reject"),
    path("<uuid:payment_id>/receipt/", PaymentReceiptView.as_view(), name="payment-receipt"),
    path("invoices/", InvoiceListCreateView.as_view(), name="invoice-list"),
    path("invoices/<uuid:invoice_id>/", InvoiceDetailView.as_view(), name="invoice-detail"),
    path("invoices/<uuid:invoice_id>/issue/", InvoiceIssueView.as_view(), name="invoice-issue"),
    path(
        "invoices/<uuid:invoice_id>/payments/", InvoicePaymentView.as_view(), name="invoice-payment"
    ),
    path("invoices/<uuid:invoice_id>/void/", InvoiceVoidView.as_view(), name="invoice-void"),
]
