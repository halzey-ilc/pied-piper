from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from shared.domain.errors import DomainValidationError

_ACTION_RE = re.compile(r"^[a-z_]+\.[a-z_]+$")
_DETAIL_KEY_RE = re.compile(r"^[a-z_]{1,40}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One audited action. ``changed_fields`` lists field *names* only, never values.

    ``details`` may carry identifiers that explain the change (e.g. the previous and the
    new owner of a reassigned record) — UUIDs only, never personal data or free text;
    names are resolved when the history is read."""

    actor_user_id: UUID | None
    action: str  # "<resource>.<verb>", e.g. "customer.updated"
    resource_type: str
    resource_id: str
    changed_fields: tuple[str, ...] = ()
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    details: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _ACTION_RE.match(self.action):
            raise DomainValidationError("Audit action must look like 'resource.verb'.")
        for key, value in self.details.items():
            if not _DETAIL_KEY_RE.match(key) or not _UUID_RE.match(str(value)):
                raise DomainValidationError("Audit details hold identifiers only.")
