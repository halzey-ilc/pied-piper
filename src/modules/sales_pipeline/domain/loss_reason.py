"""Loss reason catalog entry. Pure Python: no Django, no ORM.

A company-configurable list of why deals are lost. Entries are deactivated, never
deleted, so historical deals keep pointing at a valid reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from shared.domain.errors import DomainValidationError

MAX_LOSS_REASON_LENGTH = 100


def _clean(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise DomainValidationError("Loss reason name must not be empty.")
    if len(cleaned) > MAX_LOSS_REASON_LENGTH:
        raise DomainValidationError(
            f"Loss reason name must be at most {MAX_LOSS_REASON_LENGTH} characters."
        )
    return cleaned


@dataclass(slots=True)
class LossReason:
    id: UUID
    name: str
    is_active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(cls, *, name: str) -> LossReason:
        return cls(id=uuid4(), name=_clean(name))

    def rename(self, name: str) -> bool:
        cleaned = _clean(name)
        if cleaned == self.name:
            return False
        self.name = cleaned
        return True

    def set_active(self, *, is_active: bool) -> bool:
        if self.is_active == is_active:
            return False
        self.is_active = is_active
        return True
