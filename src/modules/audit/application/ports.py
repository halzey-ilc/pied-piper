from __future__ import annotations

from typing import Protocol

from modules.audit.domain.entry import AuditEntry


class AuditLog(Protocol):
    """Append-only audit sink. Must write in the caller's transaction/database."""

    def record(self, entry: AuditEntry) -> None: ...


class AuditTrail(Protocol):
    """Reads the audit entries of one resource, newest first (field names only, no values)."""

    def entries_for(
        self, resource_type: str, resource_id: str, *, limit: int = 100
    ) -> list[AuditEntry]: ...
