from django.urls import path

from modules.telephony.presentation.views import (
    CallAdvanceView,
    CallDetailView,
    CallEndView,
    CallListCreateView,
    CallRequestView,
)

urlpatterns = [
    path("calls/", CallListCreateView.as_view(), name="call-list"),
    path("calls/request/", CallRequestView.as_view(), name="call-request"),
    path("calls/<uuid:call_id>/", CallDetailView.as_view(), name="call-detail"),
    path("calls/<uuid:call_id>/advance/", CallAdvanceView.as_view(), name="call-advance"),
    path("calls/<uuid:call_id>/end/", CallEndView.as_view(), name="call-end"),
]
