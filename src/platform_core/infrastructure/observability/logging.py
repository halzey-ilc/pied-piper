"""Structured JSON logging with request/correlation context and secret redaction."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from platform_core.application.context import (
    current_correlation_id,
    current_request_id,
    current_tenant,
)

REDACTED = "[REDACTED]"
SENSITIVE_KEY_RE = re.compile(
    r"pass(word|wd)?|secret|token|authorization|cookie|api[_-]?key|signature|dsn|"
    r"credential|private|session|otp|card|iban|body|content|audio|transcript|message_text",
    re.IGNORECASE,
)
_URL_CREDENTIALS_RE = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/@\s:]+:[^/@\s]+@", re.I)
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")

_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
    | {"message", "asctime", "request_id", "correlation_id", "tenant"}
)


def redact_text(text: str) -> str:
    text = _URL_CREDENTIALS_RE.sub(r"\g<scheme>***:***@", text)
    return _BEARER_RE.sub("Bearer " + REDACTED, text)


def redact_value(key: str, value: Any) -> Any:
    if SENSITIVE_KEY_RE.search(key):
        return REDACTED
    if isinstance(value, dict):
        return {str(k): redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact_value(key, item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id() or "-"
        record.correlation_id = current_correlation_id() or "-"
        tenant = current_tenant()
        record.tenant = tenant.slug if tenant else "-"
        return True


class RedactionFilter(logging.Filter):
    """Redacts sensitive ``extra`` fields and credentials embedded in messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in list(vars(record).items()):
            if key not in _STANDARD_ATTRS:
                setattr(record, key, redact_value(key, value))
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: redact_value(str(k), v) for k, v in record.args.items()}
            else:
                record.args = tuple(
                    redact_text(a) if isinstance(a, str) else a for a in record.args
                )
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "correlation_id": getattr(record, "correlation_id", "-"),
            "tenant": getattr(record, "tenant", "-"),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            entry["exc"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(entry, default=str, ensure_ascii=False)
