"""A message template: a reusable text managers insert when writing to a client."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from shared.domain.errors import DomainValidationError

MAX_TITLE_LENGTH = 120
MAX_BODY_LENGTH = 4000


def _clean(value: str, *, label: str, max_length: int) -> str:
    text = (value or "").strip()
    if not text:
        raise DomainValidationError(f"{label} is required.")
    if len(text) > max_length:
        raise DomainValidationError(f"{label} must be at most {max_length} characters.")
    return text


@dataclass(slots=True)
class MessageTemplate:
    id: UUID
    title: str
    body: str
    created_by: UUID
    updated_at: datetime
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(cls, *, title: str, body: str, created_by: UUID) -> MessageTemplate:
        now = datetime.now(UTC)
        return cls(
            id=uuid4(),
            title=_clean(title, label="Title", max_length=MAX_TITLE_LENGTH),
            body=_clean(body, label="Body", max_length=MAX_BODY_LENGTH),
            created_by=created_by,
            updated_at=now,
            created_at=now,
        )

    def edit(self, *, title: str | None, body: str | None) -> None:
        if title is not None:
            self.title = _clean(title, label="Title", max_length=MAX_TITLE_LENGTH)
        if body is not None:
            self.body = _clean(body, label="Body", max_length=MAX_BODY_LENGTH)
        self.updated_at = datetime.now(UTC)
