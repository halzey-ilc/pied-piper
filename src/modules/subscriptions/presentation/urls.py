from django.urls import path

from modules.subscriptions.presentation.views import (
    CurrentSubscriptionView,
    PlanListView,
    PlanUsageView,
    SubscriptionCancelView,
    SubscriptionReactivateView,
)

urlpatterns = [
    path("", CurrentSubscriptionView.as_view(), name="subscription-current"),
    path("plans/", PlanListView.as_view(), name="subscription-plans"),
    path("usage/", PlanUsageView.as_view(), name="subscription-usage"),
    path("cancel/", SubscriptionCancelView.as_view(), name="subscription-cancel"),
    path("reactivate/", SubscriptionReactivateView.as_view(), name="subscription-reactivate"),
]
