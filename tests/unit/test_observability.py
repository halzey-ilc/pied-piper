"""Log redaction, JSON log format and execution context."""

from __future__ import annotations

import json
import logging
from uuid import uuid4

from platform_core.application.context import (
    TenantContext,
    correlation_scope,
    current_tenant,
    sanitize_id,
    tenant_scope,
)
from platform_core.infrastructure.observability.logging import (
    ContextFilter,
    JsonFormatter,
    RedactionFilter,
)


def _record(msg: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, msg, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def _format(record: logging.LogRecord) -> dict[str, object]:
    ContextFilter().filter(record)
    RedactionFilter().filter(record)
    parsed: dict[str, object] = json.loads(JsonFormatter().format(record))
    return parsed


def test_sensitive_fields_and_embedded_credentials_are_redacted() -> None:
    record = _record(
        "connect amqp://crm:hunter2@rabbitmq:5672/crm with Bearer abc.def.ghi",
        password="p@ss",
        access_token="tok",
        headers={"Authorization": "Bearer x", "Accept": "json"},
        message_text="client private message",
        customer_id="c-1",
    )
    entry = _format(record)
    dumped = json.dumps(entry)
    for secret in ("hunter2", "p@ss", "abc.def.ghi", "client private message", '"tok"'):
        assert secret not in dumped
    assert entry["customer_id"] == "c-1"
    assert entry["headers"] == {"Authorization": "[REDACTED]", "Accept": "json"}


def test_log_lines_carry_request_correlation_and_tenant() -> None:
    tenant = TenantContext(tenant_id=uuid4(), slug="acme", db_alias="tenant_acme")
    with (
        correlation_scope(request_id="req-12345678", correlation_id="corr-12345678"),
        tenant_scope(tenant),
    ):
        entry = _format(_record("hello"))
    assert entry["request_id"] == "req-12345678"
    assert entry["correlation_id"] == "corr-12345678"
    assert entry["tenant"] == "acme"
    assert current_tenant() is None  # context restored


def test_inbound_ids_must_be_log_safe() -> None:
    assert sanitize_id("abcDEF12-_.") == "abcDEF12-_."
    assert sanitize_id("bad\nINJECTED log line") is None
    assert sanitize_id("x" * 65) is None
    assert sanitize_id(None) is None
