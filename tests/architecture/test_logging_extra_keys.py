"""Guard against a logging footgun: an ``extra=`` key that collides with a
built-in LogRecord attribute makes ``logging`` raise at call time. Scan every
``logger.<level>(..., extra={...})`` literal in the source for reserved keys."""

from __future__ import annotations

import ast
import logging
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"

# Attributes present on every LogRecord (plus the two logging rejects explicitly).
_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}
_LEVELS = {"debug", "info", "warning", "error", "exception", "critical", "log"}


def _collisions(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in _LEVELS:
            continue
        for kw in node.keywords:
            if kw.arg == "extra" and isinstance(kw.value, ast.Dict):
                for key in kw.value.keys:
                    if isinstance(key, ast.Constant) and key.value in _RESERVED:
                        found.append(f"{path.name}:{key.lineno} extra key {key.value!r}")
    return found


def test_no_logging_extra_key_collides_with_reserved_attributes() -> None:
    problems = [c for path in SRC.rglob("*.py") for c in _collisions(path)]
    assert problems == [], "reserved LogRecord keys in extra=: " + "; ".join(problems)
