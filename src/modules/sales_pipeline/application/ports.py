from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol
from uuid import UUID

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


@dataclass(frozen=True, slots=True)
class StageTotal:
    stage_id: UUID
    open_count: int
    open_amount: Decimal


class PipelineRepository(Protocol):
    def add(self, pipeline: Pipeline) -> None: ...

    def get(self, pipeline_id: UUID) -> Pipeline | None: ...

    def list_all(self) -> list[Pipeline]: ...

    def get_default(self) -> Pipeline | None: ...

    def clear_default_flag(self) -> None:
        """Unset is_default on every pipeline (used before marking a new default)."""
        ...


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


class CustomerLookup(Protocol):
    """Port to the customers context to verify a linked customer exists in this tenant."""

    def exists(self, customer_id: UUID) -> bool: ...


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
