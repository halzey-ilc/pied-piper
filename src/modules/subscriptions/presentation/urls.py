from django.urls import path

from modules.subscriptions.presentation.views import (
    CurrentSubscriptionView,
    PlanListView,
    SubscriptionCancelView,
    SubscriptionReactivateView,
)

urlpatterns = [
    path("", CurrentSubscriptionView.as_view(), name="subscription-current"),
    path("plans/", PlanListView.as_view(), name="subscription-plans"),
    path("cancel/", SubscriptionCancelView.as_view(), name="subscription-cancel"),
    path("reactivate/", SubscriptionReactivateView.as_view(), name="subscription-reactivate"),
]
