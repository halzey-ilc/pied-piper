from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from modules.access_control.domain.rbac import Principal
from modules.imports.domain.rows import ImportEntity, ImportRow


@dataclass(frozen=True, slots=True)
class ExistingMatch:
    """A record already in the company with the same phone or e-mail."""

    kind: str  # "lead" | "customer"
    id: UUID
    name: str


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
