from django.conf import settings
from django.urls import URLPattern, URLResolver, include, path

from platform_core.presentation import errors, health, metrics

urlpatterns: list[URLPattern | URLResolver] = [
    path("health/live", health.liveness, name="health-live"),
    path("health/ready", health.readiness, name="health-ready"),
    path("metrics", metrics.metrics_view, name="metrics"),
    path("api/v1/", include("entrypoints.api.v1.urls")),
]

if settings.API_DOCS_ENABLED:
    from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="api-schema"),
        path("api/docs/", SpectacularSwaggerView.as_view(url_name="api-schema"), name="api-docs"),
    ]

handler400 = errors.bad_request
handler403 = errors.permission_denied
handler404 = errors.not_found
handler500 = errors.server_error
