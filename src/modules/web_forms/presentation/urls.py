from django.urls import path

from modules.web_forms.presentation.views import WebFormKeyListCreateView, WebFormKeyRevokeView

urlpatterns = [
    path("keys/", WebFormKeyListCreateView.as_view(), name="web-form-keys"),
    path("keys/<uuid:key_id>/revoke/", WebFormKeyRevokeView.as_view(), name="web-form-key-revoke"),
]
