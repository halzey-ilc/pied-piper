"""KPI read model built from events: the event -> handler -> fact path, on real PG.

Exercises the real dispatcher (registry + inbox dedupe + handler + recorder)
without Celery, by calling the dispatch helper directly under a tenant scope.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from entrypoints.workers.tasks.events import _dispatch
from modules.sales_kpi.infrastructure.models import MetricFact
from platform_core.infrastructure.tenancy.services import system_tenant_scope
from tests.conftest import ALL_DATABASES

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)
ALIAS = "tenant_acme"


def _envelope(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": str(uuid4()),
        "event_type": event_type,
        "aggregate_type": "deal",
        "aggregate_id": "1",
        "occurred_at": datetime(2026, 3, 10, 12, 0, tzinfo=UTC).isoformat(),
        "payload": payload,
        "correlation_id": None,
        "tenant_slug": "acme",
    }


def test_deal_won_event_produces_facts_idempotently(tenants: Any) -> None:
    envelope = _envelope("sales.deal_won", {"deal_id": "1", "amount": "1500.00", "currency": "USD"})
    with system_tenant_scope("acme"):
        handled = _dispatch(ALIAS, envelope)
        assert handled == 1  # the sales_kpi read-model handler
        # Redelivery of the same event does nothing (inbox dedupe).
        _dispatch(ALIAS, envelope)

    facts = {f.metric: f.value for f in MetricFact.objects.using(ALIAS).all()}
    assert facts == {
        "deal_won": Decimal("1.00"),
        "deal_closed": Decimal("1.00"),
        "revenue_won": Decimal("1500.00"),
    }
    assert MetricFact.objects.using(ALIAS).count() == 3  # not duplicated


def test_various_events_record_their_metrics(tenants: Any) -> None:
    events = [
        _envelope("sales.deal_created", {"owner_user_id": str(uuid4())}),
        _envelope("sales.deal_lost", {}),
        _envelope("leads.lead_captured", {"owner_user_id": str(uuid4())}),
        _envelope("leads.lead_converted", {}),
        _envelope("activities.activity_logged", {"owner_user_id": str(uuid4())}),
        _envelope("activities.task_completed", {}),
    ]
    with system_tenant_scope("acme"):
        for envelope in events:
            _dispatch(ALIAS, envelope)

    metrics = set(MetricFact.objects.using(ALIAS).values_list("metric", flat=True))
    assert metrics == {
        "deal_created",
        "deal_lost",
        "deal_closed",
        "lead_captured",
        "lead_converted",
        "activity_logged",
        "task_completed",
    }


def test_facts_land_only_in_their_tenant(tenants: Any) -> None:
    with system_tenant_scope("acme"):
        _dispatch(ALIAS, _envelope("sales.deal_lost", {}))
    assert MetricFact.objects.using(ALIAS).count() == 2  # deal_lost + deal_closed
    assert MetricFact.objects.using("tenant_globex").count() == 0
