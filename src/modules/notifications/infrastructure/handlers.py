"""Event handlers that create in-app notifications for the relevant user.

Run inside the dispatcher's unit of work on the active tenant database, and are
idempotent (unique recipient + source_event_id) and preference-aware.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from modules.notifications.composition import notification_service
from modules.notifications.domain.notification import NotificationCategory

CONSUMER = "notifications.inbox"


def _owner(payload: dict[str, Any]) -> UUID | None:
    raw = payload.get("owner_user_id")
    return UUID(raw) if isinstance(raw, str) else None


def _notify(message: dict[str, Any], category: NotificationCategory, title: str, body: str) -> None:
    recipient = _owner(message["payload"])
    if recipient is None:
        return
    notification_service().notify(
        recipient_user_id=recipient,
        category=category,
        title=title,
        body=body,
        source_event_id=UUID(message["event_id"]),
    )


def _on_meeting_scheduled(message: dict[str, Any]) -> None:
    _notify(message, NotificationCategory.MEETING, "Meeting scheduled", "A meeting was scheduled.")


def _on_deal_created(message: dict[str, Any]) -> None:
    _notify(
        message, NotificationCategory.DEAL, "New deal", "A deal was created and assigned to you."
    )


def _on_lead_captured(message: dict[str, Any]) -> None:
    _notify(
        message, NotificationCategory.LEAD, "New lead", "A lead was captured and assigned to you."
    )


_HANDLERS: dict[str, Callable[[dict[str, Any]], None]] = {
    "meetings.meeting_scheduled": _on_meeting_scheduled,
    "sales.deal_created": _on_deal_created,
    "leads.lead_captured": _on_lead_captured,
}


def register_handlers(registry: Any) -> None:
    for event_type, handler in _HANDLERS.items():
        if CONSUMER not in registry.handlers_for(event_type):
            registry.register(event_type, CONSUMER, handler)
