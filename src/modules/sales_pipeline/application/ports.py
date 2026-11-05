from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from modules.access_control.domain.rbac import MemberRef, Principal
from modules.sales_pipeline.domain.custom_field import CustomFieldDefinition
from modules.sales_pipeline.domain.deal import Deal, DealStatus
from modules.sales_pipeline.domain.loss_reason import LossReason
from modules.sales_pipeline.domain.pipeline import Pipeline
from modules.sales_pipeline.domain.stage_change import StageChange


@dataclass(frozen=True, slots=True)
class DealVisibility:
    """Row filter derived from the caller's RBAC scope. ``None`` owner = all deals."""

    owner_user_id: UUID | None = None
    team_ids: frozenset[UUID] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class DealFilter:
    status: DealStatus | None = None
    pipeline_id: UUID | None = None
    stage_id: UUID | None = None
    search: str | None = None
    owner_user_id: UUID | None = None  # the "manager" of the deal
    customer_id: UUID | None = None
    created_from: date | None = None
    created_to: date | None = None
    # Closed (won or lost) on/after this date — e.g. the month's sales on the board.
    closed_from: date | None = None
    # "overdue" (past expected close, still open) or "no_next_step" (open, no next step).
    attention: str | None = None
    # Company-timezone "today", supplied by the caller for the overdue cutoff.
    today: date | None = None
    # One of ORDERING_FIELDS (optionally prefixed with "-"); defaults to newest first.
    ordering: str | None = None


ORDERING_FIELDS = frozenset({"created_at", "updated_at", "closed_at", "amount", "title"})


@dataclass(frozen=True, slots=True)
class StageTotal:
    stage_id: UUID
    open_count: int
    open_amount: Decimal


class PipelineRepository(Protocol):
    def add(self, pipeline: Pipeline) -> None: ...

    def get(self, pipeline_id: UUID) -> Pipeline | None: ...

    def list_all(self) -> list[Pipeline]: ...

    def get_default(self, department_id: UUID | None = None) -> Pipeline | None:
        """The default pipeline of a department (None: the company-wide default)."""
        ...

    def clear_default_flag(self, department_id: UUID | None = None) -> None:
        """Unset is_default within one scope: a department's pipelines, or company-wide ones."""
        ...

    def get_for_update(self, pipeline_id: UUID) -> Pipeline | None:
        """Like get, but locks the pipeline until the transaction ends (configuration edits)."""
        ...

    def save(self, pipeline: Pipeline) -> None: ...


class DealRepository(Protocol):
    def add(self, deal: Deal) -> None: ...

    def get(self, deal_id: UUID) -> Deal | None: ...

    def save(self, deal: Deal, expected_version: int) -> None:
        """Optimistic concurrency: ConflictError if the stored version differs."""
        ...

    def list_visible(
        self, visibility: DealVisibility, filters: DealFilter, *, limit: int, offset: int
    ) -> list[Deal]: ...

    def count(self, visibility: DealVisibility, filters: DealFilter) -> int: ...

    def has_open_deals_in_pipeline(self, pipeline_id: UUID) -> bool: ...

    def stage_counts(self, pipeline_id: UUID) -> dict[UUID, tuple[int, int]]:
        """Per stage of a pipeline: (open deals, all deals), whoever owns them."""
        ...

    def move_all_on_stage(self, stage_id: UUID, to_stage_id: UUID) -> list[tuple[UUID, int]]:
        """Move every deal of a stage (any status) to another stage of the same pipeline;
        bumps each deal's version. Returns (deal id, new version) per moved deal."""
        ...

    def stage_ever_used(self, stage_id: UUID) -> bool:
        """A deal stands on the stage now or did in the past (stage history)."""
        ...

    def open_deal_counts(self, pipeline_id: UUID, owner_ids: set[UUID]) -> dict[UUID, int]:
        """Open deals per stage of a pipeline, only those owned by ``owner_ids``."""
        ...

    def move_open_deals(
        self,
        from_pipeline_id: UUID,
        to_pipeline_id: UUID,
        stage_map: dict[UUID, UUID],
        owner_ids: set[UUID],
    ) -> list[tuple[UUID, UUID, UUID, int]]:
        """Move the open deals of ``owner_ids`` from one pipeline to another, each stage to
        its mapped stage; bumps versions. Returns (deal id, from stage, to stage, version)."""
        ...


@dataclass(frozen=True, slots=True)
class LinkedCustomer:
    """What a deal needs to know about a customer it links to."""

    owner_user_id: UUID
    team_id: UUID | None
    is_archived: bool


class CustomerLookup(Protocol):
    """Port to the customers context: the customer a deal links to, in this tenant."""

    def find(self, customer_id: UUID) -> LinkedCustomer | None: ...


class DepartmentRoster(Protocol):
    """Port to the organizations context: active members of a sales department."""

    def member_ids(self, department_id: UUID) -> set[UUID]: ...


class MemberDirectory(Protocol):
    """Active members of the company (a deal can be handed only to one of them)."""

    def active_member(self, user_id: UUID) -> MemberRef | None: ...


class LossReasonRepository(Protocol):
    def add(self, reason: LossReason) -> None: ...

    def get(self, reason_id: UUID) -> LossReason | None: ...

    def list_all(self, *, active_only: bool = False) -> list[LossReason]: ...

    def save(self, reason: LossReason) -> None: ...

    def exists_by_name(self, name: str) -> bool: ...


class StageChangeRepository(Protocol):
    def add(self, change: StageChange) -> None: ...

    def list_for_deal(self, deal_id: UUID) -> list[StageChange]: ...


class CustomFieldRepository(Protocol):
    def add(self, definition: CustomFieldDefinition) -> None: ...

    def get(self, definition_id: UUID) -> CustomFieldDefinition | None: ...

    def list_all(self, *, active_only: bool = False) -> list[CustomFieldDefinition]: ...

    def save(self, definition: CustomFieldDefinition) -> None: ...

    def exists_by_key(self, key: str) -> bool: ...


class DealMeetings(Protocol):
    """Meetings of a deal, for the «Встреча перенесена» stage (implemented by meetings)."""

    def reschedule_next(
        self, principal: Principal, deal: Deal, start: datetime, end: datetime
    ) -> tuple[UUID, datetime | None]:
        """Move the deal's nearest scheduled (or awaiting a new date) meeting to the new
        time, or book one for the deal owner. Returns the meeting and its previous start
        (None for a newly booked meeting)."""
        ...
