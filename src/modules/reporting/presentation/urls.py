from django.urls import path

from modules.reporting.presentation.views import (
    ReportCreateView,
    ReportDetailView,
    ReportDownloadView,
)

urlpatterns = [
    path("", ReportCreateView.as_view(), name="report-create"),
    path("<uuid:job_id>/", ReportDetailView.as_view(), name="report-detail"),
    path("<uuid:job_id>/download/", ReportDownloadView.as_view(), name="report-download"),
]
