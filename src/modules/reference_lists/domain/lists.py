"""Company reference lists: reasons a meeting was cancelled or the client did not come, and
the labels of lead sources.

Meeting reasons are free-form company lists (added, renamed, switched off — never deleted, so
past choices stay readable). Lead sources are the fixed codes of the leads API (an enum other
modules and integrations rely on): a company only renames them and switches them off for its
staff, it cannot invent new codes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from modules.leads.domain.lead import LeadSource
from shared.domain.errors import DomainValidationError

MAX_LABEL_LENGTH = 100
MAX_ITEMS = 50


class ListKey(StrEnum):
    MEETING_CANCEL = "meeting_cancel"
    MEETING_NO_SHOW = "meeting_no_show"
    LEAD_SOURCE = "lead_source"

    @property
    def is_fixed(self) -> bool:
        """Fixed lists hold only predefined codes: items are renamed or hidden, not added."""
        return self is ListKey.LEAD_SOURCE


LEAD_SOURCE_LABELS: dict[str, str] = {
    LeadSource.WEBSITE.value: "Сайт",
    LeadSource.REFERRAL.value: "Рекомендация",
    LeadSource.WHATSAPP.value: "WhatsApp",
    LeadSource.TELEGRAM.value: "Telegram",
    LeadSource.INSTAGRAM.value: "Instagram",
    LeadSource.PHONE.value: "Звонок",
    LeadSource.IMPORT.value: "Импорт",
    LeadSource.OTHER.value: "Другое",
}


@dataclass(frozen=True, slots=True)
class ListItem:
    id: UUID | None  # None — a fixed item the company has not changed yet
    code: str | None  # the fixed code (lead sources); None for free-form items
    label: str
    is_active: bool
    position: int


def clean_label(label: str) -> str:
    cleaned = " ".join(label.split())
    if not cleaned:
        raise DomainValidationError("Label is required.")
    if len(cleaned) > MAX_LABEL_LENGTH:
        raise DomainValidationError(f"Label must be at most {MAX_LABEL_LENGTH} characters.")
    return cleaned


def parse_key(raw: str) -> ListKey:
    try:
        return ListKey(raw)
    except ValueError as exc:
        raise DomainValidationError(
            "Unknown list. Use meeting_cancel, meeting_no_show or lead_source."
        ) from exc
