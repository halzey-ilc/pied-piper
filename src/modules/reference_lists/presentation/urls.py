from django.urls import path

from modules.reference_lists.presentation.views import (
    ReferenceItemView,
    ReferenceListView,
    ReferenceOrderView,
)

urlpatterns = [
    path("<str:key>/", ReferenceListView.as_view(), name="reference-list"),
    path("<str:key>/order/", ReferenceOrderView.as_view(), name="reference-list-order"),
    path("<str:key>/items/<str:item>/", ReferenceItemView.as_view(), name="reference-list-item"),
]
