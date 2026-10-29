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


# --- AI results: tell the person who asked (or, for daily digests, the person it is for).

_REPORTS: dict[str, tuple[str, str]] = {
    "operations_digest": ("AI operations digest", "/api/v1/ai/operations/digest/"),
    "kpi_explanation": ("AI KPI explanation", "/api/v1/ai/analytics/kpi-explanation/"),
}


def _uuid(payload: dict[str, Any], key: str) -> UUID | None:
    raw = payload.get(key)
    return UUID(raw) if isinstance(raw, str) else None


def _notify_ai(message: dict[str, Any], recipient_key: str, what: str, link: str) -> None:
    payload = message["payload"]
    recipient = _uuid(payload, recipient_key)
    if recipient is None:
        return  # events emitted before the recipient was part of the payload
    if payload.get("succeeded") is True:
        title, body = f"{what} is ready", "Check it before acting on it: AI results can be wrong."
    else:
        title, body = f"{what} failed", "The AI Platform did not return a result. Try again later."
    notification_service().notify(
        recipient_user_id=recipient,
        category=NotificationCategory.AI,
        title=title,
        body=body,
        source_event_id=UUID(message["event_id"]),
        link=link,
    )


def _on_ai_report_completed(message: dict[str, Any]) -> None:
    known = _REPORTS.get(str(message["payload"].get("kind")))
    if known is not None:
        _notify_ai(message, "user_id", *known)


def _on_call_insight_completed(message: dict[str, Any]) -> None:
    call_id = _uuid(message["payload"], "call_id")
    if call_id is not None:
        _notify_ai(
            message, "requested_by", "AI call summary", f"/api/v1/ai/calls/{call_id}/summary/"
        )


def _on_reply_suggestion_completed(message: dict[str, Any]) -> None:
    conversation_id = _uuid(message["payload"], "conversation_id")
    if conversation_id is not None:
        _notify_ai(
            message,
            "requested_by",
            "AI reply suggestion",
            f"/api/v1/ai/conversations/{conversation_id}/reply/",
        )


def _on_deal_brief_completed(message: dict[str, Any]) -> None:
    deal_id = _uuid(message["payload"], "deal_id")
    if deal_id is not None:
        _notify_ai(message, "requested_by", "AI deal brief", f"/api/v1/ai/deals/{deal_id}/brief/")


_HANDLERS: dict[str, Callable[[dict[str, Any]], None]] = {
    "ai_insights.deal_brief_completed": _on_deal_brief_completed,
    "meetings.meeting_scheduled": _on_meeting_scheduled,
    "sales.deal_created": _on_deal_created,
    "leads.lead_captured": _on_lead_captured,
    "ai_insights.report_completed": _on_ai_report_completed,
    "ai_insights.call_insight_completed": _on_call_insight_completed,
    "ai_insights.reply_suggestion_completed": _on_reply_suggestion_completed,
}


def register_handlers(registry: Any) -> None:
    for event_type, handler in _HANDLERS.items():
        if CONSUMER not in registry.handlers_for(event_type):
            registry.register(event_type, CONSUMER, handler)
