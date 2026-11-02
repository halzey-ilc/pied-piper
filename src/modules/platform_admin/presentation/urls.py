from django.urls import path

from modules.platform_admin.presentation.views import (
    PlatformCatalogView,
    PlatformCompaniesView,
    PlatformCompanyDetailView,
    PlatformCompanyPaymentsView,
    PlatformCompanySubscriptionView,
    PlatformDirectorCredentialsView,
    PlatformErrorDetailView,
    PlatformErrorsView,
    PlatformOverviewView,
    PlatformPaymentCancelView,
    PlatformPaymentConfirmView,
    PlatformPaymentsView,
    PlatformTicketDetailView,
    PlatformTicketsView,
)

urlpatterns = [
    path("overview/", PlatformOverviewView.as_view(), name="platform-overview"),
    path("catalog/", PlatformCatalogView.as_view(), name="platform-catalog"),
    path("companies/", PlatformCompaniesView.as_view(), name="platform-companies"),
    path(
        "companies/<uuid:company_id>/",
        PlatformCompanyDetailView.as_view(),
        name="platform-company",
    ),
    path(
        "companies/<uuid:company_id>/directors/<uuid:user_id>/credentials/",
        PlatformDirectorCredentialsView.as_view(),
        name="platform-director-credentials",
    ),
    path(
        "companies/<uuid:company_id>/subscription/",
        PlatformCompanySubscriptionView.as_view(),
        name="platform-company-subscription",
    ),
    path(
        "companies/<uuid:company_id>/payments/",
        PlatformCompanyPaymentsView.as_view(),
        name="platform-company-payments",
    ),
    path("payments/", PlatformPaymentsView.as_view(), name="platform-payments"),
    path(
        "payments/<uuid:payment_id>/confirm/",
        PlatformPaymentConfirmView.as_view(),
        name="platform-payment-confirm",
    ),
    path(
        "payments/<uuid:payment_id>/cancel/",
        PlatformPaymentCancelView.as_view(),
        name="platform-payment-cancel",
    ),
    path("errors/", PlatformErrorsView.as_view(), name="platform-errors"),
    path("errors/<uuid:error_id>/", PlatformErrorDetailView.as_view(), name="platform-error"),
    path("tickets/", PlatformTicketsView.as_view(), name="platform-tickets"),
    path("tickets/<uuid:ticket_id>/", PlatformTicketDetailView.as_view(), name="platform-ticket"),
]
