from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from shared.domain.errors import DomainValidationError

_ACTION_RE = re.compile(r"^[a-z_]+\.[a-z_]+$")


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One audited action. ``changed_fields`` lists field *names* only, never values."""

    actor_user_id: UUID | None
    action: str  # "<resource>.<verb>", e.g. "customer.updated"
    resource_type: str
    resource_id: str
    changed_fields: tuple[str, ...] = ()
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not _ACTION_RE.match(self.action):
            raise DomainValidationError("Audit action must look like 'resource.verb'.")
