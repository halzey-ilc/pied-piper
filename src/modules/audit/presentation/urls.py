from django.urls import path

from modules.audit.presentation.views import AuditJournalView

urlpatterns = [
    path("", AuditJournalView.as_view(), name="audit-journal"),
]
