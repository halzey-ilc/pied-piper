from __future__ import annotations

from typing import Protocol

from modules.audit.domain.entry import AuditEntry


class AuditLog(Protocol):
    """Append-only audit sink. Must write in the caller's transaction/database."""

    def record(self, entry: AuditEntry) -> None: ...
