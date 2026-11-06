from django.urls import path

from modules.communications.presentation.template_views import (
    ChannelsView,
    TemplateDetailView,
    TemplateListView,
)
from modules.communications.presentation.views import (
    AssignView,
    CloseView,
    ConversationDetailView,
    ConversationListView,
    MessagesView,
    ReadView,
    ReopenView,
    WhatsAppQrView,
    WhatsAppStatusView,
)

urlpatterns = [
    path("templates/", TemplateListView.as_view(), name="message-template-list"),
    path(
        "templates/<uuid:template_id>/",
        TemplateDetailView.as_view(),
        name="message-template-detail",
    ),
    path("channels/", ChannelsView.as_view(), name="communication-channels"),
    path("whatsapp/status/", WhatsAppStatusView.as_view(), name="whatsapp-status"),
    path("whatsapp/qr/", WhatsAppQrView.as_view(), name="whatsapp-qr"),
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
