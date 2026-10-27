"""API v1 composition: mounts each module's presentation routes."""

from django.urls import include, path

from modules.identity.presentation.views import MeView, TokenObtainView, TokenRefreshThrottledView
from modules.integrations.presentation.views import WebhookView

urlpatterns = [
    path("auth/token/", TokenObtainView.as_view(), name="auth-token"),
    path("auth/token/refresh/", TokenRefreshThrottledView.as_view(), name="auth-token-refresh"),
    path("me/", MeView.as_view(), name="me"),
    path("customers/", include("modules.customers.presentation.urls")),
    path("leads/", include("modules.leads.presentation.urls")),
    path("sales/", include("modules.sales_pipeline.presentation.urls")),
    path("activities/", include("modules.activities.presentation.urls")),
    path("analytics/kpis/", include("modules.sales_kpi.presentation.urls")),
    path("analytics/reports/", include("modules.reporting.presentation.urls")),
    path(
        "webhooks/<str:provider>/<slug:tenant_slug>/",
        WebhookView.as_view(),
        name="webhook",
    ),
]
