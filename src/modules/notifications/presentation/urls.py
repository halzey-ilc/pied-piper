from django.urls import path

from modules.notifications.presentation.views import (
    MarkAllReadView,
    MarkReadView,
    NotificationListView,
    PreferencesView,
    UnreadCountView,
)

urlpatterns = [
    path("", NotificationListView.as_view(), name="notification-list"),
    path("unread-count/", UnreadCountView.as_view(), name="notification-unread-count"),
    path("read-all/", MarkAllReadView.as_view(), name="notification-read-all"),
    path("preferences/", PreferencesView.as_view(), name="notification-preferences"),
    path("<uuid:notification_id>/read/", MarkReadView.as_view(), name="notification-read"),
]
