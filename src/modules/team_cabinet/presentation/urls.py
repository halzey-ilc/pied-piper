from django.urls import path

from modules.team_cabinet.presentation.views import TeamInactivityView, TeamTodayView

urlpatterns = [
    path("today/", TeamTodayView.as_view(), name="team-today"),
    path("inactivity/", TeamInactivityView.as_view(), name="team-inactivity"),
]
