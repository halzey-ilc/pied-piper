"""Reads conversations through the communications module's public composition API.

Visibility reuses communications' own RBAC check (``ConversationService.get``),
so this module cannot see a conversation the user could not open in the CRM.
"""

from __future__ import annotations

from uuid import UUID

from modules.access_control.domain.rbac import Principal
from modules.ai_insights.application.reply_ports import ConversationSnapshot
from modules.ai_insights.domain.reply import MessageSnapshot
from modules.communications.composition import conversation_repository, conversation_service


class CommunicationsConversationReader:
    def get_visible(self, principal: Principal, conversation_id: UUID) -> ConversationSnapshot:
        conversation = conversation_service().get(principal, conversation_id)  # NotFoundError
        return ConversationSnapshot(
            id=conversation.id,
            owner_user_id=conversation.owner_user_id,
            team_id=conversation.team_id,
            channel=conversation.channel.value,
        )

    def channel_of(self, conversation_id: UUID) -> str | None:
        conversation = conversation_repository().get(conversation_id)
        return None if conversation is None else conversation.channel.value

    def recent_messages(self, conversation_id: UUID, limit: int) -> list[MessageSnapshot]:
        return [
            MessageSnapshot(id=m.id, direction=m.direction.value, text=m.text)
            for m in conversation_repository().recent_messages(conversation_id, limit=limit)
        ]
