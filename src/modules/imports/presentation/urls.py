from django.urls import path

from modules.imports.presentation.views import ImportListCreateView, ImportPreviewView

urlpatterns = [
    path("", ImportListCreateView.as_view(), name="import-list"),
    path("preview/", ImportPreviewView.as_view(), name="import-preview"),
]
