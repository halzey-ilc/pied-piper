"""Notification aggregate and categories. Pure Python: no Django, no ORM."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from shared.domain.errors import DomainValidationError

MAX_TITLE_LENGTH = 200
MAX_BODY_LENGTH = 1000


class NotificationCategory(StrEnum):
    MEETING = "meeting"
    DEAL = "deal"
    LEAD = "lead"
    PAYMENT = "payment"
    SYSTEM = "system"


def _clean(text: str, *, label: str, max_length: int, required: bool) -> str:
    cleaned = " ".join(text.split()) if text else ""
    if not cleaned and required:
        raise DomainValidationError(f"{label} is required.")
    if len(cleaned) > max_length:
        raise DomainValidationError(f"{label} must be at most {max_length} characters.")
    return cleaned


@dataclass(slots=True)
class Notification:
    id: UUID
    recipient_user_id: UUID
    category: NotificationCategory
    title: str
    body: str
    source_event_id: UUID | None = None
    link: str | None = None
    read: bool = False
    read_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        *,
        recipient_user_id: UUID,
        category: NotificationCategory,
        title: str,
        body: str,
        source_event_id: UUID | None = None,
        link: str | None = None,
    ) -> Notification:
        return cls(
            id=uuid4(),
            recipient_user_id=recipient_user_id,
            category=category,
            title=_clean(title, label="Title", max_length=MAX_TITLE_LENGTH, required=True),
            body=_clean(body, label="Body", max_length=MAX_BODY_LENGTH, required=False),
            source_event_id=source_event_id,
            link=link,
        )

    def mark_read(self) -> bool:
        if self.read:
            return False
        self.read = True
        self.read_at = datetime.now(UTC)
        return True
