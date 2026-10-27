from django.urls import path

from modules.sales_kpi.presentation.views import KpiHistoryView, KpiView

urlpatterns = [
    path("", KpiView.as_view(), name="kpi-current"),
    path("history/", KpiHistoryView.as_view(), name="kpi-history"),
]
