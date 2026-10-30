from django.urls import path

from modules.planning.presentation.views import (
    AbsenceListCreateView,
    PlanHistoryView,
    PlanListSetView,
)

urlpatterns = [
    path("plans/", PlanListSetView.as_view(), name="plan-list"),
    path("plans/<uuid:plan_id>/history/", PlanHistoryView.as_view(), name="plan-history"),
    path("absences/", AbsenceListCreateView.as_view(), name="absence-list"),
]
