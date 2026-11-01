from django.urls import path

from modules.organizations.presentation.views import (
    CompanyView,
    DepartmentDetailView,
    DepartmentsView,
    StaffCreateOptionsView,
    StaffDeactivateView,
    StaffDetailView,
    StaffNamesView,
    StaffRequestApproveView,
    StaffRequestCancelView,
    StaffRequestRejectView,
    StaffRequestsView,
    StaffResetPasswordView,
    StaffView,
)

urlpatterns = [
    path("company/", CompanyView.as_view(), name="company"),
    path("departments/", DepartmentsView.as_view(), name="departments"),
    path("departments/<uuid:department_id>/", DepartmentDetailView.as_view(), name="department"),
    path("staff/", StaffView.as_view(), name="staff"),
    path("staff/names/", StaffNamesView.as_view(), name="staff-names"),
    path("staff/requests/", StaffRequestsView.as_view(), name="staff-requests"),
    path(
        "staff/requests/<uuid:request_id>/approve/",
        StaffRequestApproveView.as_view(),
        name="staff-request-approve",
    ),
    path(
        "staff/requests/<uuid:request_id>/reject/",
        StaffRequestRejectView.as_view(),
        name="staff-request-reject",
    ),
    path(
        "staff/requests/<uuid:request_id>/cancel/",
        StaffRequestCancelView.as_view(),
        name="staff-request-cancel",
    ),
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
