from django.urls import path

from modules.operations.presentation.views import FindingsView, SettingsView

urlpatterns = [
    path("findings/", FindingsView.as_view(), name="operations-findings"),
    path("settings/", SettingsView.as_view(), name="operations-settings"),
]
