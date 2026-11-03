from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid4

from modules.access_control.domain.rbac import MemberRef, Principal
from modules.activities.application.next_step import NextStep
from modules.imports.domain.rows import ImportEntity, ImportRow
from modules.sales_pipeline.domain.loss_reason import LossReason
from modules.sales_pipeline.domain.pipeline import Pipeline


@dataclass(frozen=True, slots=True)
class ExistingMatch:
    """A record already in the company with the same phone or e-mail."""

    kind: str  # "lead" | "customer"
    id: UUID
    name: str
    # A customer's responsible member and team (who may link deals to it) and archive flag.
    owner_user_id: UUID | None = None
    team_id: UUID | None = None
    is_archived: bool = False


@dataclass(frozen=True, slots=True)
class RowError:
    line: int
    message: str


@dataclass(frozen=True, slots=True)
class ImportJob:
    entity: ImportEntity
    filename: str
    created_by: UUID
    owner_user_id: UUID
    total: int
    created: int
    duplicates: int
    invalid: int
    failed: int
    errors: tuple[RowError, ...] = ()
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class ExistingContacts(Protocol):
    """Finds records of the company that already have one of these phones or e-mails."""

    def find(
        self, entity: ImportEntity, *, phones: set[str], emails: set[str]
    ) -> dict[str, ExistingMatch]:
        """Keyed by the matching phone (E.164) or e-mail (lower case)."""
        ...


class RecordCreator(Protocol):
    """Creates one lead or customer through its module's service (rules, events, audit)
    and hands it to ``owner_user_id`` when that is not the importing user."""

    def create(
        self, principal: Principal, entity: ImportEntity, row: ImportRow, owner_user_id: UUID
    ) -> UUID: ...


class ImportJobStore(Protocol):
    def add(self, job: ImportJob) -> None: ...

    def recent(self, limit: int) -> list[ImportJob]: ...


@dataclass(frozen=True, slots=True)
class NewCustomer:
    name: str
    phone: str | None
    email: str | None


@dataclass(frozen=True, slots=True)
class DealDraft:
    """A deal row resolved against the company: what the deal services are asked to do.

    The deal is opened on the pipeline's first stage (as any new deal), handed to
    ``owner_user_id``, and then moved to ``stage_id`` when that is another stage — the same
    move a user makes, with its next step or loss reason."""

    title: str
    amount: Decimal
    currency: str
    pipeline_id: UUID
    stage_id: UUID
    move_to_stage: bool
    owner_user_id: UUID
    customer_id: UUID | None = None
    new_customer: NewCustomer | None = None
    source: str | None = None
    expected_close_date: date | None = None
    next_step: NextStep | None = None
    loss_reason_id: UUID | None = None
    loss_reason: str | None = None


@dataclass(frozen=True, slots=True)
class CreatedDeal:
    deal_id: UUID
    customer_id: UUID | None


class DealCreator(Protocol):
    """Creates one deal (and its new customer) through the customers and sales_pipeline
    services in one transaction: a refused step leaves nothing of the row behind."""

    def create(self, principal: Principal, draft: DealDraft) -> CreatedDeal: ...


class DealReferences(Protocol):
    """What a deal row is resolved against, read with the importing member's rights."""

    def pipelines(self, principal: Principal) -> list[Pipeline]:
        """Pipelines visible to the member (their department's first)."""
        ...

    def default_pipeline(self, department_id: UUID | None) -> Pipeline | None:
        """Where a new deal of this department goes when no pipeline is given."""
        ...

    def loss_reasons(self) -> list[LossReason]:
        """Active loss reasons of the company's catalog."""
        ...

    def members_by_login(self) -> dict[str, MemberRef]:
        """Active members keyed by their login and by their e-mail (lower case)."""
        ...

    def member(self, user_id: UUID) -> MemberRef | None: ...

    def existing_deals(self, titles: set[str]) -> set[tuple[str, UUID | None]]:
        """(lower-case title, customer id) of the company's deals with one of these titles."""
        ...

    def default_currency(self) -> str: ...
