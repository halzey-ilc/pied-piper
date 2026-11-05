from django.urls import path

from modules.team_cabinet.presentation.views import (
    MyFocusView,
    MyMonthView,
    TeamFunnelView,
    TeamInactivityView,
    TeamLossesView,
    TeamMemberCardView,
    TeamMemberScoresView,
    TeamPlanFactView,
    TeamReportsView,
    TeamTodayView,
)

urlpatterns = [
    path("today/", TeamTodayView.as_view(), name="team-today"),
    path("my-month/", MyMonthView.as_view(), name="team-my-month"),
    path("my-focus/", MyFocusView.as_view(), name="team-my-focus"),
    path("inactivity/", TeamInactivityView.as_view(), name="team-inactivity"),
    path("plan-fact/", TeamPlanFactView.as_view(), name="team-plan-fact"),
    path("reports/", TeamReportsView.as_view(), name="team-reports"),
    path("members/<uuid:user_id>/card/", TeamMemberCardView.as_view(), name="team-member-card"),
    path(
        "members/<uuid:user_id>/scores/",
        TeamMemberScoresView.as_view(),
        name="team-member-scores",
    ),
    path("funnel/", TeamFunnelView.as_view(), name="team-funnel"),
    path("losses/", TeamLossesView.as_view(), name="team-losses"),
]
