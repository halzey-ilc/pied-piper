from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from modules.sales_pipeline.domain.deal import Deal, DealStatus
from modules.sales_pipeline.domain.pipeline import Pipeline


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
