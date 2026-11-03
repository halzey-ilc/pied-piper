from django.urls import path

from modules.integrations.presentation.status_views import (
    IntegrationCheckView,
    IntegrationListView,
)

urlpatterns = [
    path("", IntegrationListView.as_view(), name="integration-list"),
    path("<str:provider>/check/", IntegrationCheckView.as_view(), name="integration-check"),
]
