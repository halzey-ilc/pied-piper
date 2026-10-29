from django.urls import path

from modules.risk.presentation.views import SettingsView, SignalsView

urlpatterns = [
    path("signals/", SignalsView.as_view(), name="risk-signals"),
    path("settings/", SettingsView.as_view(), name="risk-settings"),
]
