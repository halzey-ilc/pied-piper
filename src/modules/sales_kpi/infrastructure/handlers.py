"""Event handlers that build the KPI read model from domain events.

Each handler receives the full event envelope and records one or more metric
facts. It runs inside the dispatcher's unit of work on the active tenant
database (set by system_tenant_scope), so facts and the inbox row commit
together. Recording is idempotent (unique source_event_id + metric).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from modules.sales_kpi.application.catalog import (
    METRIC_ACTIVITY_LOGGED,
    METRIC_DEAL_CLOSED,
    METRIC_DEAL_CREATED,
    METRIC_DEAL_LOST,
    METRIC_DEAL_WON,
    METRIC_LEAD_CAPTURED,
    METRIC_LEAD_CONVERTED,
    METRIC_REVENUE_WON,
    METRIC_TASK_COMPLETED,
)
from modules.sales_kpi.infrastructure.read_model import DjangoMetricRecorder
from platform_core.application.context import require_tenant

CONSUMER = "sales_kpi.read_model"
_ONE = Decimal(1)

# One fact tuple: (metric, value, owner_user_id or None)
Fact = tuple[str, Decimal, UUID | None]


def _owner(payload: dict[str, Any]) -> UUID | None:
    raw = payload.get("owner_user_id")
    return UUID(raw) if isinstance(raw, str) else None


def _amount(payload: dict[str, Any]) -> Decimal:
    try:
        return Decimal(str(payload.get("amount", "0")))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def _record(message: dict[str, Any], facts: list[Fact]) -> None:
    recorder = DjangoMetricRecorder(require_tenant().db_alias)
    event_id = UUID(message["event_id"])
    occurred_at = datetime.fromisoformat(message["occurred_at"])
    for metric, value, owner in facts:
        recorder.record(
            source_event_id=event_id,
            metric=metric,
            value=value,
            occurred_at=occurred_at,
            owner_user_id=owner,
        )


def _on_deal_created(message: dict[str, Any]) -> None:
    _record(message, [(METRIC_DEAL_CREATED, _ONE, _owner(message["payload"]))])


def _on_deal_won(message: dict[str, Any]) -> None:
    payload = message["payload"]
    _record(
        message,
        [
            (METRIC_DEAL_WON, _ONE, None),
            (METRIC_DEAL_CLOSED, _ONE, None),
            (METRIC_REVENUE_WON, _amount(payload), None),
        ],
    )


def _on_deal_lost(message: dict[str, Any]) -> None:
    _record(message, [(METRIC_DEAL_LOST, _ONE, None), (METRIC_DEAL_CLOSED, _ONE, None)])


def _on_lead_captured(message: dict[str, Any]) -> None:
    _record(message, [(METRIC_LEAD_CAPTURED, _ONE, _owner(message["payload"]))])


def _on_lead_converted(message: dict[str, Any]) -> None:
    _record(message, [(METRIC_LEAD_CONVERTED, _ONE, None)])


def _on_activity_logged(message: dict[str, Any]) -> None:
    _record(message, [(METRIC_ACTIVITY_LOGGED, _ONE, _owner(message["payload"]))])


def _on_task_completed(message: dict[str, Any]) -> None:
    _record(message, [(METRIC_TASK_COMPLETED, _ONE, None)])


_HANDLERS: dict[str, Callable[[dict[str, Any]], None]] = {
    "sales.deal_created": _on_deal_created,
    "sales.deal_won": _on_deal_won,
    "sales.deal_lost": _on_deal_lost,
    "leads.lead_captured": _on_lead_captured,
    "leads.lead_converted": _on_lead_converted,
    "activities.activity_logged": _on_activity_logged,
    "activities.task_completed": _on_task_completed,
}


def register_handlers(registry: Any) -> None:
    for event_type, handler in _HANDLERS.items():
        if CONSUMER not in registry.handlers_for(event_type):
            registry.register(event_type, CONSUMER, handler)
