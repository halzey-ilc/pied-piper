from django.urls import path

from modules.workspace.presentation.views import EffectiveLayoutView, LayoutView

urlpatterns = [
    path("layout/", LayoutView.as_view(), name="workspace-layout"),
    path("layout/effective/", EffectiveLayoutView.as_view(), name="workspace-layout-effective"),
]
