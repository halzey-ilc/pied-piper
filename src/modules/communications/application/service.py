"""Conversation use cases. User-facing operations enforce RBAC; inbound ingest
is called by the webhook pipeline (system) and is idempotent."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from modules.access_control.domain.rbac import (
    AccessPolicy,
    Permission,
    Principal,
    ResourceOwnership,
    Scope,
)
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.communications.application.ports import (
    ChannelSender,
    ConversationFilter,
    ConversationRepository,
    ConversationVisibility,
)
from modules.communications.domain.conversation import (
    Channel,
    Conversation,
    Message,
    MessageStatus,
)
from platform_core.application.ports import EventOutbox
from shared.application.ports import UnitOfWork
from shared.domain.errors import NotFoundError

AGGREGATE = "conversation"
_Action = Callable[["Conversation"], object]


@dataclass(frozen=True, slots=True)
class InboundMessage:
    channel: Channel
    external_contact_id: str
    external_message_id: str
    text: str
    occurred_at: datetime
    contact_name: str | None = None


@dataclass(frozen=True, slots=True)
class ConversationPage:
    items: list[Conversation]
    total: int


def _ownership(conversation: Conversation) -> ResourceOwnership:
    return ResourceOwnership(owner_user_id=conversation.owner_user_id, team_id=conversation.team_id)


class ConversationService:
    def __init__(
        self,
        repository: ConversationRepository,
        sender: ChannelSender,
        uow: UnitOfWork,
        outbox: EventOutbox,
        audit: AuditLog,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._repo = repository
        self._sender = sender
        self._uow = uow
        self._outbox = outbox
        self._audit = audit
        self._policy = policy or AccessPolicy()

    # --- system: inbound ----------------------------------------------------

    def ingest_inbound(self, inbound: InboundMessage) -> bool:
        """Append an inbound message, creating the conversation if needed. Idempotent
        on the provider message id. Returns True if a new message was stored."""
        with self._uow:
            if self._repo.message_exists(inbound.channel, inbound.external_message_id):
                return False
            conversation = self._repo.get_by_contact(inbound.channel, inbound.external_contact_id)
            new_conversation = conversation is None
            if conversation is None:
                conversation = Conversation.start(
                    channel=inbound.channel,
                    external_contact_id=inbound.external_contact_id,
                    contact_name=inbound.contact_name,
                    created_at=inbound.occurred_at,
                )
            version = conversation.version
            message, event = conversation.receive(
                text=inbound.text,
                external_id=inbound.external_message_id,
                occurred_at=inbound.occurred_at,
            )
            if new_conversation:
                self._repo.add(conversation)
            else:
                self._repo.save(conversation, expected_version=version)
            self._repo.add_message(conversation.id, message)
            self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(conversation.id))
        return True

    # --- reads --------------------------------------------------------------

    def list_conversations(
        self, principal: Principal, filters: ConversationFilter, *, limit: int, offset: int
    ) -> ConversationPage:
        scope = self._policy.require(principal, Permission.COMMUNICATIONS_VIEW)
        visibility = self._visibility(principal, scope)
        return ConversationPage(
            items=self._repo.list_visible(visibility, filters, limit=limit, offset=offset),
            total=self._repo.count(visibility, filters),
        )

    def get(self, principal: Principal, conversation_id: UUID) -> Conversation:
        conversation = self._repo.get(conversation_id)
        if conversation is None or not self._visible(principal, conversation):
            raise NotFoundError("Conversation not found.")
        return conversation

    def messages(
        self, principal: Principal, conversation_id: UUID, *, limit: int, offset: int
    ) -> list[Message]:
        self.get(principal, conversation_id)  # visibility check
        return self._repo.list_messages(conversation_id, limit=limit, offset=offset)

    def _visible(self, principal: Principal, conversation: Conversation) -> bool:
        scope = self._policy.scope_for(principal, Permission.COMMUNICATIONS_VIEW)
        if scope is None:
            return False
        if scope is Scope.ALL:
            return True
        # Unassigned conversations are visible to everyone who can view, so they
        # can be picked up; otherwise ownership applies.
        if conversation.owner_user_id is None:
            return True
        return self._policy.allows(
            principal, Permission.COMMUNICATIONS_VIEW, _ownership(conversation)
        )

    def _visibility(self, principal: Principal, scope: Scope) -> ConversationVisibility:
        if scope is Scope.ALL:
            return ConversationVisibility()
        if scope is Scope.TEAM:
            return ConversationVisibility(
                owner_user_id=principal.user_id,
                team_ids=principal.team_ids,
                include_unassigned=True,
            )
        return ConversationVisibility(owner_user_id=principal.user_id, include_unassigned=True)

    # --- writes -------------------------------------------------------------

    def send_message(self, principal: Principal, conversation_id: UUID, text: str) -> Message:
        self._policy.require(principal, Permission.COMMUNICATIONS_SEND)
        with self._uow:
            conversation = self.get(principal, conversation_id)
            version = conversation.version
            message, event = conversation.send(
                text=text, sender_user_id=principal.user_id, occurred_at=_now()
            )
            external_id = self._sender.send(
                channel=conversation.channel,
                to_contact=conversation.external_contact_id,
                text=message.text,
            )
            message.external_id = external_id
            message.status = MessageStatus.SENT
            self._repo.save(conversation, expected_version=version)
            self._repo.add_message(conversation.id, message)
            self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(conversation.id))
            self._record(principal, "conversation.message_sent", conversation)
        return message

    def assign_to_me(self, principal: Principal, conversation_id: UUID) -> Conversation:
        self._policy.require(principal, Permission.COMMUNICATIONS_SEND)
        with self._uow:
            conversation = self.get(principal, conversation_id)
            version = conversation.version
            conversation.assign(principal.user_id)
            self._repo.save(conversation, expected_version=version)
            self._record(principal, "conversation.assigned", conversation)
        return conversation

    def mark_read(self, principal: Principal, conversation_id: UUID) -> Conversation:
        return self._mutate(
            principal, conversation_id, lambda c: c.mark_read(), "conversation.read"
        )

    def close(self, principal: Principal, conversation_id: UUID) -> Conversation:
        return self._mutate(principal, conversation_id, lambda c: c.close(), "conversation.closed")

    def reopen(self, principal: Principal, conversation_id: UUID) -> Conversation:
        return self._mutate(
            principal, conversation_id, lambda c: c.reopen(), "conversation.reopened"
        )

    def _mutate(
        self, principal: Principal, conversation_id: UUID, action: _Action, audit_action: str
    ) -> Conversation:
        self._policy.require(principal, Permission.COMMUNICATIONS_SEND)
        with self._uow:
            conversation = self.get(principal, conversation_id)
            version = conversation.version
            action(conversation)
            self._repo.save(conversation, expected_version=version)
            self._record(principal, audit_action, conversation)
        return conversation

    def _record(self, principal: Principal, action: str, conversation: Conversation) -> None:
        self._audit.record(
            AuditEntry(
                actor_user_id=principal.user_id,
                action=action,
                resource_type=AGGREGATE,
                resource_id=str(conversation.id),
            )
        )


def _now() -> datetime:
    return datetime.now(UTC)
