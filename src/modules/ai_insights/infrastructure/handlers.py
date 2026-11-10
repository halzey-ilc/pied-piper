"""Event handlers of the AI module: an answered call with a recording from a connected PBX
is analysed (transcript and review). Telephony publishes the event and knows nothing of AI."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from modules.ai_insights.composition import call_analysis_service
from modules.telephony.composition import call_repository, connection_settings
from modules.telephony.domain.call import CallStatus

CONSUMER = "ai_insights.call_analysis"


def on_call_ended(message: dict[str, Any]) -> None:
    payload = message["payload"]
    if payload.get("status") != CallStatus.COMPLETED.value:
        return
    connection = connection_settings()
    if connection is None or not connection.analyze_calls:
        return
    call = call_repository().get(UUID(payload["call_id"]))
    if call is None or not call.recording_url:
        return
    call_analysis_service().start(call.id)


_HANDLERS = {"telephony.call_ended": on_call_ended}


def register_handlers(registry: Any) -> None:
    for event_type, handler in _HANDLERS.items():
        if CONSUMER not in registry.handlers_for(event_type):
            registry.register(event_type, CONSUMER, handler)
