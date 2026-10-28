"""API v1 composition: mounts each module's presentation routes."""

from django.urls import include, path

from modules.identity.presentation.views import (
    MeView,
    MfaConfirmView,
    MfaDisableView,
    MfaEnrollView,
    OidcLoginView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    TokenObtainView,
    TokenRefreshThrottledView,
)
from modules.integrations.presentation.views import WebhookView

urlpatterns = [
    path("auth/token/", TokenObtainView.as_view(), name="auth-token"),
    path("auth/token/refresh/", TokenRefreshThrottledView.as_view(), name="auth-token-refresh"),
    path("auth/oidc/", OidcLoginView.as_view(), name="auth-oidc"),
    path("auth/mfa/enroll/", MfaEnrollView.as_view(), name="auth-mfa-enroll"),
    path("auth/mfa/confirm/", MfaConfirmView.as_view(), name="auth-mfa-confirm"),
    path("auth/mfa/disable/", MfaDisableView.as_view(), name="auth-mfa-disable"),
    path(
        "auth/password-reset/",
        PasswordResetRequestView.as_view(),
        name="auth-password-reset",
    ),
    path(
        "auth/password-reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="auth-password-reset-confirm",
    ),
    path("me/", MeView.as_view(), name="me"),
    path("customers/", include("modules.customers.presentation.urls")),
    path("leads/", include("modules.leads.presentation.urls")),
    path("sales/", include("modules.sales_pipeline.presentation.urls")),
    path("activities/", include("modules.activities.presentation.urls")),
    path("meetings/", include("modules.meetings.presentation.urls")),
    path("payments/", include("modules.payments.presentation.urls")),
    path("telephony/", include("modules.telephony.presentation.urls")),
    path("subscription/", include("modules.subscriptions.presentation.urls")),
    path("notifications/", include("modules.notifications.presentation.urls")),
    path("communications/", include("modules.communications.presentation.urls")),
    path("analytics/kpis/", include("modules.sales_kpi.presentation.urls")),
    path("analytics/reports/", include("modules.reporting.presentation.urls")),
    path(
        "webhooks/<str:provider>/<slug:tenant_slug>/",
        WebhookView.as_view(),
        name="webhook",
    ),
]
