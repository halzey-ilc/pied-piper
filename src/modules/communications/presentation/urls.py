from django.urls import path

from modules.communications.presentation.views import (
    AssignView,
    CloseView,
    ConversationDetailView,
    ConversationListView,
    MessagesView,
    ReadView,
    ReopenView,
)

urlpatterns = [
    path("conversations/", ConversationListView.as_view(), name="conversation-list"),
    path(
        "conversations/<uuid:conversation_id>/",
        ConversationDetailView.as_view(),
        name="conversation-detail",
    ),
    path(
        "conversations/<uuid:conversation_id>/messages/",
        MessagesView.as_view(),
        name="conversation-messages",
    ),
    path(
        "conversations/<uuid:conversation_id>/assign/",
        AssignView.as_view(),
        name="conversation-assign",
    ),
    path(
        "conversations/<uuid:conversation_id>/read/", ReadView.as_view(), name="conversation-read"
    ),
    path(
        "conversations/<uuid:conversation_id>/close/",
        CloseView.as_view(),
        name="conversation-close",
    ),
    path(
        "conversations/<uuid:conversation_id>/reopen/",
        ReopenView.as_view(),
        name="conversation-reopen",
    ),
]
