"""Test-only URLconf with a view that fails unexpectedly."""

from django.urls import path
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from config.urls import urlpatterns as project_urlpatterns


class ExplodingView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request: Request) -> Response:
        raise RuntimeError("db password is hunter2")  # must never reach the client


urlpatterns = [path("boom/", ExplodingView.as_view()), *project_urlpatterns]
