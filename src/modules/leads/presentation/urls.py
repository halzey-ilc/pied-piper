from django.urls import path

from modules.leads.presentation.views import (
    LeadAssignView,
    LeadConvertView,
    LeadDetailView,
    LeadDisqualifyView,
    LeadDuplicatesView,
    LeadHistoryView,
    LeadListCreateView,
    LeadQualifyView,
)

urlpatterns = [
    path("", LeadListCreateView.as_view(), name="lead-list"),
    path("<uuid:lead_id>/", LeadDetailView.as_view(), name="lead-detail"),
    path("<uuid:lead_id>/qualify/", LeadQualifyView.as_view(), name="lead-qualify"),
    path("<uuid:lead_id>/disqualify/", LeadDisqualifyView.as_view(), name="lead-disqualify"),
    path("<uuid:lead_id>/convert/", LeadConvertView.as_view(), name="lead-convert"),
    path("<uuid:lead_id>/duplicates/", LeadDuplicatesView.as_view(), name="lead-duplicates"),
    path("<uuid:lead_id>/assign/", LeadAssignView.as_view(), name="lead-assign"),
    path("<uuid:lead_id>/history/", LeadHistoryView.as_view(), name="lead-history"),
]
