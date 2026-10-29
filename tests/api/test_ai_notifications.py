"""Notifications for finished AI results: who gets them, links, muting, idempotency,
and the full path from a finished AI job to the notification."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from rest_framework.test import APIClient

from entrypoints.workers.tasks.events import _dispatch
from modules.ai_insights import composition
from modules.ai_insights.application.ports import AiJobState
from modules.notifications.infrastructure.models import NotificationRecord
from platform_core.infrastructure.messaging.models import OutboxMessage
from platform_core.infrastructure.tenancy.services import system_tenant_scope
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)
ALIAS = "tenant_acme"


def _event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": str(uuid4()),
        "event_type": event_type,
        "aggregate_type": "ai",
        "aggregate_id": "1",
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": payload,
        "correlation_id": None,
        "tenant_slug": "acme",
    }


def _notifications(user_id: Any) -> list[NotificationRecord]:
    return list(NotificationRecord.objects.using(ALIAS).filter(recipient_user_id=user_id))


def test_each_ai_result_notifies_the_requester_with_a_link(
    make_user: Callable[..., Any], tenants: dict[str, TenantFixture]
) -> None:
    user = make_user("owner", tenant=tenants["acme"])
    call_id, conversation_id = uuid4(), uuid4()
    events = [
        _event(
            "ai_insights.report_completed",
            {
                "report_id": "r",
                "kind": "operations_digest",
                "user_id": str(user.pk),
                "succeeded": True,
            },
        ),
        _event(
            "ai_insights.call_insight_completed",
            {
                "call_id": str(call_id),
                "kind": "call_summary",
                "requested_by": str(user.pk),
                "succeeded": True,
            },
        ),
        _event(
            "ai_insights.reply_suggestion_completed",
            {
                "conversation_id": str(conversation_id),
                "requested_by": str(user.pk),
                "succeeded": False,
            },
        ),
    ]
    with system_tenant_scope("acme"):
        for event in events:
            _dispatch(ALIAS, event)
            _dispatch(ALIAS, event)  # redelivery creates nothing new

    by_link = {n.link: n for n in _notifications(user.pk)}
    assert set(by_link) == {
        "/api/v1/ai/operations/digest/",
        f"/api/v1/ai/calls/{call_id}/summary/",
        f"/api/v1/ai/conversations/{conversation_id}/reply/",
    }
    assert all(n.category == "ai" for n in by_link.values())
    assert by_link["/api/v1/ai/operations/digest/"].title == "AI operations digest is ready"
    assert "failed" in by_link[f"/api/v1/ai/conversations/{conversation_id}/reply/"].title


def test_old_events_and_unknown_kinds_are_ignored(
    make_user: Callable[..., Any], tenants: dict[str, TenantFixture]
) -> None:
    user = make_user("owner", tenant=tenants["acme"])
    with system_tenant_scope("acme"):
        # Emitted before requested_by was part of the payload: nobody to notify.
        _dispatch(
            ALIAS,
            _event(
                "ai_insights.call_insight_completed", {"call_id": str(uuid4()), "succeeded": True}
            ),
        )
        _dispatch(
            ALIAS,
            _event(
                "ai_insights.report_completed",
                {"kind": "future_kind", "user_id": str(user.pk), "succeeded": True},
            ),
        )
    assert _notifications(user.pk) == []


def test_ai_notifications_can_be_muted(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    user = make_user("owner", tenant=tenants["acme"])
    client = client_for(user, "acme")
    response = client.post(
        "/api/v1/notifications/preferences/", {"category": "ai", "enabled": False}, format="json"
    )
    assert response.status_code == 200, response.content
    assert response.json()["categories"]["ai"] is False
    with system_tenant_scope("acme"):
        _dispatch(
            ALIAS,
            _event(
                "ai_insights.report_completed",
                {"kind": "kpi_explanation", "user_id": str(user.pk), "succeeded": True},
            ),
        )
    assert _notifications(user.pk) == []


def test_finished_reply_suggestion_emits_event_that_notifies(
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    from tests.api.test_ai_reply_suggestions import CHAT, OUTPUT, _conversation

    class Fake:
        def submit(self, **_: Any) -> AiJobState:
            return AiJobState(job_id="job-1", status="queued")

        def fetch(self, **kw: Any) -> AiJobState:
            return AiJobState(
                job_id=kw["job_id"],
                status="succeeded",
                schema_id="message_reply.output.v1",
                provider="mock",
                model="m",
                output=OUTPUT,
            )

    settings.AI_PLATFORM = {
        **settings.AI_PLATFORM,
        "ENABLED": True,
        "URL": "http://ai.test",
        "TOKEN_KEY": "api-test-ai-platform-key-0123456789abcdef",
    }
    monkeypatch.setattr(composition, "ai_platform_client", Fake)
    user = make_user("owner", tenant=tenants["acme"])
    conversation = _conversation(ALIAS, user.pk, *CHAT)
    suggestion_id = UUID(
        client_for(user, "acme")
        .post(f"/api/v1/ai/conversations/{conversation.id}/reply/")
        .json()["id"]
    )
    with system_tenant_scope("acme"):
        service = composition.reply_suggestion_service()
        service.submit(suggestion_id)
        service.poll(suggestion_id)
        row = OutboxMessage.objects.using(ALIAS).get(
            event_type="ai_insights.reply_suggestion_completed"
        )
        _dispatch(
            ALIAS,
            {
                "event_id": str(row.id),
                "event_type": row.event_type,
                "payload": row.payload,
                "tenant_slug": "acme",
                "correlation_id": None,
            },
        )
    [notification] = _notifications(user.pk)
    assert notification.title == "AI reply suggestion is ready"
