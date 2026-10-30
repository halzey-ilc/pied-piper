"""Deal aggregate. Pure Python: no Django, no ORM.

Lifecycle:

    OPEN ──win──> WON
      │
      ├──lose──> LOST
      │
      └──move_to(stage)──> OPEN (different stage)

    WON / LOST ──reopen(stage)──> OPEN
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID, uuid4

from shared.domain.errors import ConflictError, DomainValidationError
from shared.domain.events import DomainEvent
from shared.domain.value_objects import Money

MAX_TITLE_LENGTH = 200
MAX_REASON_LENGTH = 500
MAX_SOURCE_LENGTH = 100
MAX_NEXT_STEP_LENGTH = 500


def _clean_optional(text: str | None, max_length: int) -> str | None:
    """Collapse whitespace; an empty string becomes None (the field is cleared)."""
    if text is None:
        return None
    cleaned = " ".join(text.split())
    if not cleaned:
        return None
    return cleaned[:max_length]


class DealStatus(StrEnum):
    OPEN = "open"
    WON = "won"
    LOST = "lost"


class DealStateError(ConflictError):
    code = "deal_invalid_state"


def _clean_title(title: str) -> str:
    cleaned = " ".join(title.split())
    if not cleaned:
        raise DomainValidationError("Deal title must not be empty.")
    if len(cleaned) > MAX_TITLE_LENGTH:
        raise DomainValidationError(f"Deal title must be at most {MAX_TITLE_LENGTH} characters.")
    return cleaned


@dataclass(frozen=True, kw_only=True)
class DealCreated(DomainEvent):
    event_type: ClassVar[str] = "sales.deal_created"
    deal_id: UUID
    pipeline_id: UUID
    owner_user_id: UUID

    def payload(self) -> dict[str, Any]:
        return {
            "deal_id": str(self.deal_id),
            "pipeline_id": str(self.pipeline_id),
            "owner_user_id": str(self.owner_user_id),
        }


@dataclass(frozen=True, kw_only=True)
class DealStageChanged(DomainEvent):
    event_type: ClassVar[str] = "sales.deal_stage_changed"
    deal_id: UUID
    from_stage_id: UUID
    to_stage_id: UUID

    def payload(self) -> dict[str, Any]:
        return {
            "deal_id": str(self.deal_id),
            "from_stage_id": str(self.from_stage_id),
            "to_stage_id": str(self.to_stage_id),
        }


@dataclass(frozen=True, kw_only=True)
class DealWon(DomainEvent):
    event_type: ClassVar[str] = "sales.deal_won"
    deal_id: UUID
    amount: str
    currency: str

    def payload(self) -> dict[str, Any]:
        return {"deal_id": str(self.deal_id), "amount": self.amount, "currency": self.currency}


@dataclass(frozen=True, kw_only=True)
class DealLost(DomainEvent):
    event_type: ClassVar[str] = "sales.deal_lost"
    deal_id: UUID

    def payload(self) -> dict[str, Any]:
        return {"deal_id": str(self.deal_id)}


@dataclass(frozen=True, kw_only=True)
class DealReopened(DomainEvent):
    event_type: ClassVar[str] = "sales.deal_reopened"
    deal_id: UUID

    def payload(self) -> dict[str, Any]:
        return {"deal_id": str(self.deal_id)}


@dataclass(slots=True)
class Deal:
    id: UUID
    title: str
    pipeline_id: UUID
    stage_id: UUID
    value: Money
    owner_user_id: UUID
    status: DealStatus = DealStatus.OPEN
    customer_id: UUID | None = None
    team_id: UUID | None = None
    source: str | None = None
    next_step: str | None = None
    expected_close_date: date | None = None
    closed_at: datetime | None = None
    lost_reason: str | None = None
    loss_reason_id: UUID | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    version: int = 1

    @classmethod
    def open(
        cls,
        *,
        title: str,
        pipeline_id: UUID,
        stage_id: UUID,
        value: Money,
        owner_user_id: UUID,
        customer_id: UUID | None = None,
        team_id: UUID | None = None,
        source: str | None = None,
        next_step: str | None = None,
        expected_close_date: date | None = None,
    ) -> tuple[Deal, DealCreated]:
        deal = cls(
            id=uuid4(),
            title=_clean_title(title),
            pipeline_id=pipeline_id,
            stage_id=stage_id,
            value=value,
            owner_user_id=owner_user_id,
            customer_id=customer_id,
            team_id=team_id,
            source=_clean_optional(source, MAX_SOURCE_LENGTH),
            next_step=_clean_optional(next_step, MAX_NEXT_STEP_LENGTH),
            expected_close_date=expected_close_date,
        )
        return deal, DealCreated(
            deal_id=deal.id, pipeline_id=pipeline_id, owner_user_id=owner_user_id
        )

    def _require_open(self, action: str) -> None:
        if self.status is not DealStatus.OPEN:
            raise DealStateError(f"A {self.status.value} deal cannot be {action}.")

    def update_details(
        self,
        *,
        title: str | None = None,
        value: Money | None = None,
        customer_id: UUID | None = None,
        set_customer: bool = False,
        source: str | None = None,
        set_source: bool = False,
        next_step: str | None = None,
        set_next_step: bool = False,
        expected_close_date: date | None = None,
        set_expected_close: bool = False,
    ) -> bool:
        """Edit an open deal. ``set_*`` flags allow clearing a field to None."""
        self._require_open("edited")
        changed = False
        if title is not None and (cleaned := _clean_title(title)) != self.title:
            self.title, changed = cleaned, True
        if value is not None and value != self.value:
            self.value, changed = value, True
        if set_customer and customer_id != self.customer_id:
            self.customer_id, changed = customer_id, True
        if set_source and (new_source := _clean_optional(source, MAX_SOURCE_LENGTH)) != self.source:
            self.source, changed = new_source, True
        if set_next_step:
            new_next = _clean_optional(next_step, MAX_NEXT_STEP_LENGTH)
            if new_next != self.next_step:
                self.next_step, changed = new_next, True
        if set_expected_close and expected_close_date != self.expected_close_date:
            self.expected_close_date, changed = expected_close_date, True
        if changed:
            self.version += 1
        return changed

    def move_to(self, stage_id: UUID) -> DealStageChanged | None:
        self._require_open("moved to another stage")
        if stage_id == self.stage_id:
            return None
        event = DealStageChanged(deal_id=self.id, from_stage_id=self.stage_id, to_stage_id=stage_id)
        self.stage_id = stage_id
        self.version += 1
        return event

    def win(self, *, stage_id: UUID | None = None) -> DealWon:
        """Close the deal as won. ``stage_id`` relocates it to the won stage."""
        self._require_open("won")
        self.status = DealStatus.WON
        if stage_id is not None:
            self.stage_id = stage_id
        self.closed_at = datetime.now(UTC)
        self.lost_reason = None
        self.loss_reason_id = None
        self.version += 1
        return DealWon(deal_id=self.id, amount=str(self.value.amount), currency=self.value.currency)

    def lose(
        self, reason: str, *, stage_id: UUID | None = None, loss_reason_id: UUID | None = None
    ) -> DealLost:
        """Close the deal as lost. ``stage_id`` relocates it to the lost stage;
        ``loss_reason_id`` links a catalog reason alongside the free-text note."""
        self._require_open("lost")
        cleaned = " ".join(reason.split())
        if not cleaned:
            raise DomainValidationError("A loss reason is required.")
        self.status = DealStatus.LOST
        if stage_id is not None:
            self.stage_id = stage_id
        self.closed_at = datetime.now(UTC)
        self.lost_reason = cleaned[:MAX_REASON_LENGTH]
        self.loss_reason_id = loss_reason_id
        self.version += 1
        return DealLost(deal_id=self.id)

    def reopen(self, stage_id: UUID) -> DealReopened:
        if self.status is DealStatus.OPEN:
            raise DealStateError("The deal is already open.")
        self.status = DealStatus.OPEN
        self.stage_id = stage_id
        self.closed_at = None
        self.lost_reason = None
        self.loss_reason_id = None
        self.version += 1
        return DealReopened(deal_id=self.id)
