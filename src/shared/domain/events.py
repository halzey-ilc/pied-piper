"""Domain event base type."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import UUID, uuid4


@dataclass(frozen=True, kw_only=True)
class DomainEvent:
    """Something that happened in the domain. Subclasses set ``event_type``.

    ``payload()`` must contain identifiers and changed field names only: no
    secrets and no free-form customer content.
    """

    event_type: ClassVar[str] = "domain.event"

    event_id: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def payload(self) -> dict[str, Any]:
        return {}
