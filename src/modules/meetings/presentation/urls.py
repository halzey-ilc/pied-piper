from django.urls import path

from modules.meetings.presentation.views import (
    MeetingCancelView,
    MeetingCompleteView,
    MeetingConfirmView,
    MeetingDetailView,
    MeetingListCreateView,
    MeetingOutcomeView,
    MeetingParticipantsView,
    MeetingRescheduleView,
    MeetingRespondView,
)

urlpatterns = [
    path("", MeetingListCreateView.as_view(), name="meeting-list"),
    path("<uuid:meeting_id>/", MeetingDetailView.as_view(), name="meeting-detail"),
    path(
        "<uuid:meeting_id>/reschedule/", MeetingRescheduleView.as_view(), name="meeting-reschedule"
    ),
    path("<uuid:meeting_id>/complete/", MeetingCompleteView.as_view(), name="meeting-complete"),
    path("<uuid:meeting_id>/cancel/", MeetingCancelView.as_view(), name="meeting-cancel"),
    path("<uuid:meeting_id>/confirm/", MeetingConfirmView.as_view(), name="meeting-confirm"),
    path("<uuid:meeting_id>/outcome/", MeetingOutcomeView.as_view(), name="meeting-outcome"),
    path(
        "<uuid:meeting_id>/participants/",
        MeetingParticipantsView.as_view(),
        name="meeting-participants",
    ),
    path(
        "<uuid:meeting_id>/participants/respond/",
        MeetingRespondView.as_view(),
        name="meeting-respond",
    ),
]
