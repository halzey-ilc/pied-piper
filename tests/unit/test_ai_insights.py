"""AI insights: domain lifecycle, AI Platform HTTP client, configuration rules."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import httpx
import jwt
import pytest

from config.settings.env import AppEnv, hardening_errors
from modules.ai_insights.domain.insight import (
    CallInsight,
    InsightKind,
    InsightStatus,
    source_fingerprint,
)
from modules.ai_insights.infrastructure.client import HttpAiPlatformClient
from shared.application.retry import PermanentError, TransientError
from shared.domain.errors import DomainValidationError

KEY = "unit-test-ai-platform-key-0123456789abcdef"


def _insight() -> CallInsight:
    return CallInsight.request(
        call_id=uuid4(), kind=InsightKind.CALL_SUMMARY, requested_by=uuid4(), source_text="Hi."
    )


def _status(insight: CallInsight) -> InsightStatus:
    # Read through a function so mypy does not narrow the status across transitions.
    return insight.status


# --- domain -------------------------------------------------------------------


def test_lifecycle_and_event() -> None:
    insight = _insight()
    assert _status(insight) is InsightStatus.PENDING
    assert insight.source_hash == source_fingerprint("Hi.")
    insight.mark_submitted("job-1")
    event = insight.mark_succeeded(
        result={"summary": "Hi."},
        schema_id="call_summary.output.v1",
        provider="mock",
        model="m",
        is_mock=True,
    )
    assert _status(insight) is InsightStatus.SUCCEEDED and insight.finished_at is not None
    # Events carry identifiers only, never the analysed text or the result.
    assert set(event.payload()) == {"insight_id", "call_id", "kind", "requested_by", "succeeded"}
    with pytest.raises(DomainValidationError):
        insight.mark_failed("late")


def test_requests_v2_and_accepts_v1_results_in_flight() -> None:
    insight = _insight()
    assert insight.job_type == "call.summary.v2"
    insight.mark_submitted("job-1")
    insight.mark_succeeded(
        result={"summary": "Недостаточно данных.", "insufficient_data": True},
        schema_id="call_summary.output.v2",
        provider="mock",
        model="m",
        is_mock=True,
    )
    older = _insight()
    older.mark_submitted("job-2")
    older.mark_succeeded(  # submitted as v1 before the switch
        result={"summary": "Hi."},
        schema_id="call_summary.output.v1",
        provider="mock",
        model="m",
        is_mock=True,
    )
    assert _status(insight) is _status(older) is InsightStatus.SUCCEEDED


def test_rejects_empty_text_and_foreign_contract() -> None:
    with pytest.raises(DomainValidationError):
        CallInsight.request(
            call_id=uuid4(), kind=InsightKind.CALL_SUMMARY, requested_by=uuid4(), source_text="  "
        )
    insight = _insight()
    with pytest.raises(DomainValidationError):
        insight.mark_succeeded(
            result={}, schema_id="call_summary.output.v1", provider="p", model="m", is_mock=False
        )  # not submitted yet
    insight.mark_submitted("job-1")
    with pytest.raises(DomainValidationError):
        insight.mark_succeeded(
            result={}, schema_id="something.else.v9", provider="p", model="m", is_mock=False
        )


# --- HTTP client --------------------------------------------------------------


def _client(handler: Any) -> HttpAiPlatformClient:
    return HttpAiPlatformClient(
        base_url="http://ai.test",
        token_key=KEY,
        issuer="crm-platform",
        audience="crm-ai-platform",
        transport=httpx.MockTransport(handler),
    )


def _job(status: str = "queued", **extra: Any) -> dict[str, Any]:
    return {"schema": "ai.job.v1", "id": "job-123", "status": status, **extra}


def test_submit_sends_tenant_bound_short_lived_token() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"].removeprefix("Bearer ")
        seen["tenant"] = request.headers["X-Tenant"]
        seen["key"] = request.headers["Idempotency-Key"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(202, json=_job())

    state = _client(handler).submit(
        tenant_slug="acme",
        subject="user:42",
        job_type="call.summary.v1",
        payload={"transcript": "Hi.", "language": "unknown"},
        idempotency_key="crm-insight-1",
    )
    assert state.job_id == "job-123" and state.status == "queued"
    claims = jwt.decode(seen["auth"], KEY, algorithms=["HS256"], audience="crm-ai-platform")
    assert claims["tenant"] == "acme" and seen["tenant"] == "acme"
    assert claims["sub"] == "user:42" and claims["iss"] == "crm-platform"
    assert claims["scope"] == "ai.jobs.create"
    assert claims["exp"] - claims["iat"] <= 300
    assert seen["key"] == "crm-insight-1"
    assert seen["body"] == {
        "type": "call.summary.v1",
        "input": {"transcript": "Hi.", "language": "unknown"},
    }


def test_fetch_parses_result() -> None:
    body = _job(
        "succeeded",
        result={
            "schema_id": "call_summary.output.v1",
            "provider": "mock",
            "model": "m",
            "output": {"summary": "Hi."},
        },
    )
    state = _client(lambda r: httpx.Response(200, json=body)).fetch(
        tenant_slug="acme", subject="user:1", job_id="job-123"
    )
    assert state.is_terminal and state.output == {"summary": "Hi."}


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_retryable_statuses_are_transient(code: int) -> None:
    client = _client(lambda r: httpx.Response(code, json={}))
    with pytest.raises(TransientError):
        client.fetch(tenant_slug="acme", subject="user:1", job_id="x")


def test_network_failure_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(TransientError):
        _client(handler).fetch(tenant_slug="acme", subject="user:1", job_id="x")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"schema": "ai.error.v1", "error": {"code": "unauthenticated"}}),
        httpx.Response(422, json={"schema": "ai.error.v1", "error": {"code": "invalid_input"}}),
        httpx.Response(200, json={"schema": "something.else"}),
        httpx.Response(200, text="not json"),
    ],
)
def test_rejections_and_contract_mismatch_are_permanent(response: httpx.Response) -> None:
    with pytest.raises(PermanentError):
        _client(lambda r: response).fetch(tenant_slug="acme", subject="user:1", job_id="x")


# --- configuration ------------------------------------------------------------


def _env(**overrides: Any) -> AppEnv:
    base: dict[str, Any] = {
        "DJANGO_ENV": "test",
        "DJANGO_SECRET_KEY": "s" * 60,
        "JWT_SIGNING_KEY": "j" * 60,
        "DB_PASSWORD": "db-password-value",
        "RABBITMQ_PASSWORD": "mq-password-value",
    }
    return AppEnv(**{**base, **overrides})


def _ai_errors(env: AppEnv) -> list[str]:
    return [e for e in hardening_errors(env) if "AI_PLATFORM" in e]


def test_disabled_by_default_needs_nothing() -> None:
    env = _env()
    assert env.AI_PLATFORM_ENABLED is False
    assert _ai_errors(env) == []


def _enabled(key: str | None) -> AppEnv:
    return _env(
        AI_PLATFORM_ENABLED=True, AI_PLATFORM_URL="http://ai:8090", AI_PLATFORM_TOKEN_KEY=key
    )


def test_enabled_requires_url_and_strong_distinct_key() -> None:
    assert len(_ai_errors(_env(AI_PLATFORM_ENABLED=True))) == 2
    assert _ai_errors(_enabled("k")), "short key must be refused"
    assert _ai_errors(_enabled("j" * 60)), "reusing the CRM's JWT key must be refused"
    assert _ai_errors(_enabled("__GENERATE__" * 4)), "placeholder must be refused"
    assert _ai_errors(_enabled(KEY)) == []


def test_errors_never_contain_the_key() -> None:
    secret = "tiny-but-secret"
    errors = hardening_errors(
        _env(AI_PLATFORM_ENABLED=True, AI_PLATFORM_URL="http://ai", AI_PLATFORM_TOKEN_KEY=secret)
    )
    assert errors and all(secret not in e for e in errors)
