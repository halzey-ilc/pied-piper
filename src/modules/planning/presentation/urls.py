from django.urls import path

from modules.planning.presentation.views import (
    AbsenceListCreateView,
    PlanHistoryView,
    PlanListSetView,
    PlanProgressView,
    WorkSessionCurrentView,
    WorkSessionListView,
    WorkSessionStartView,
    WorkSessionStopView,
)

urlpatterns = [
    path("plans/", PlanListSetView.as_view(), name="plan-list"),
    path("plans/progress/", PlanProgressView.as_view(), name="plan-progress"),
    path("plans/<uuid:plan_id>/history/", PlanHistoryView.as_view(), name="plan-history"),
    path("work-sessions/", WorkSessionListView.as_view(), name="work-session-list"),
    path("work-sessions/start/", WorkSessionStartView.as_view(), name="work-session-start"),
    path("work-sessions/stop/", WorkSessionStopView.as_view(), name="work-session-stop"),
    path("work-sessions/current/", WorkSessionCurrentView.as_view(), name="work-session-current"),
    path("absences/", AbsenceListCreateView.as_view(), name="absence-list"),
]
