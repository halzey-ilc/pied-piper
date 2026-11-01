from django.urls import path

from modules.organizations.presentation.views import (
    CompanyView,
    DepartmentDetailView,
    DepartmentsView,
    StaffCreateOptionsView,
    StaffDeactivateView,
    StaffDetailView,
    StaffNamesView,
    StaffResetPasswordView,
    StaffView,
)

urlpatterns = [
    path("company/", CompanyView.as_view(), name="company"),
    path("departments/", DepartmentsView.as_view(), name="departments"),
    path("departments/<uuid:department_id>/", DepartmentDetailView.as_view(), name="department"),
    path("staff/", StaffView.as_view(), name="staff"),
    path("staff/names/", StaffNamesView.as_view(), name="staff-names"),
    path("staff/create-options/", StaffCreateOptionsView.as_view(), name="staff-create-options"),
    path("staff/<uuid:membership_id>/", StaffDetailView.as_view(), name="staff-member"),
    path(
        "staff/<uuid:membership_id>/deactivate/",
        StaffDeactivateView.as_view(),
        name="staff-deactivate",
    ),
    path(
        "staff/<uuid:membership_id>/reset-password/",
        StaffResetPasswordView.as_view(),
        name="staff-reset-password",
    ),
]
