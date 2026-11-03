"""Import of leads or customers from a CSV file: preview first, then commit.

Duplicates are never created: a row whose phone or e-mail already belongs to a record of
the company (or appears earlier in the same file) is skipped and reported. Every created
record goes through its module's service, so ownership, events and audit apply as for a
record created by hand.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.imports.application.ports import (
    ExistingContacts,
    ExistingMatch,
    ImportJob,
    ImportJobStore,
    RecordCreator,
    RowError,
)
from modules.imports.domain.rows import ImportEntity, ImportRow, parse_rows
from shared.application.ports import UnitOfWork
from shared.domain.errors import DomainError

MAX_REPORTED_ERRORS = 50

_CREATE_PERMISSION = {
    ImportEntity.LEADS: Permission.LEADS_CREATE,
    ImportEntity.CUSTOMERS: Permission.CUSTOMERS_CREATE,
}


class RowStatus(StrEnum):
    NEW = "new"
    DUPLICATE = "duplicate"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class PreviewRow:
    row: ImportRow
    status: RowStatus
    reason: str | None = None
    duplicate_of: ExistingMatch | None = None
    duplicate_line: int | None = None


@dataclass(frozen=True, slots=True)
class Preview:
    entity: ImportEntity
    rows: list[PreviewRow]

    def count(self, status: RowStatus) -> int:
        return sum(1 for r in self.rows if r.status is status)


class ImportService:
    def __init__(
        self,
        contacts: ExistingContacts,
        creator: RecordCreator,
        jobs: ImportJobStore,
        audit: AuditLog,
        uow: UnitOfWork,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._contacts = contacts
        self._creator = creator
        self._jobs = jobs
        self._audit = audit
        self._uow = uow
        self._policy = policy or AccessPolicy()

    def preview(self, principal: Principal, entity: ImportEntity, text: str) -> Preview:
        self._policy.require(principal, _CREATE_PERMISSION[entity])
        rows = parse_rows(text, entity)
        existing = self._contacts.find(
            entity,
            phones={r.phone for r in rows if r.phone and not r.errors},
            emails={r.email for r in rows if r.email and not r.errors},
        )
        seen: dict[str, int] = {}
        result: list[PreviewRow] = []
        for row in rows:
            if row.errors:
                result.append(PreviewRow(row, RowStatus.INVALID, "; ".join(row.errors)))
                continue
            keys = [k for k in (row.phone, row.email) if k]
            match = next((existing[k] for k in keys if k in existing), None)
            earlier = next((seen[k] for k in keys if k in seen), None)
            if match is not None:
                kind = "клиент" if match.kind == "customer" else "лид"
                result.append(
                    PreviewRow(row, RowStatus.DUPLICATE, f"Уже есть {kind} «{match.name}»", match)
                )
            elif earlier is not None:
                result.append(
                    PreviewRow(
                        row,
                        RowStatus.DUPLICATE,
                        f"Повторяет строку {earlier} этого файла",
                        duplicate_line=earlier,
                    )
                )
            else:
                result.append(PreviewRow(row, RowStatus.NEW))
            for key in keys:
                seen.setdefault(key, row.line)
        return Preview(entity, result)

    def commit(
        self,
        principal: Principal,
        entity: ImportEntity,
        filename: str,
        text: str,
        owner_user_id: UUID | None = None,
    ) -> ImportJob:
        """Creates the rows the preview marks as new; reports the rest. Rows are created one
        by one, so one rejected row does not undo the others."""
        preview = self.preview(principal, entity, text)
        owner = owner_user_id or principal.user_id
        created = failed = 0
        errors: list[RowError] = []
        for item in preview.rows:
            if item.status is not RowStatus.NEW:
                if item.status is RowStatus.INVALID:
                    errors.append(RowError(item.row.line, item.reason or "Ошибка в строке"))
                continue
            try:
                self._creator.create(principal, entity, item.row, owner)
                created += 1
            except DomainError as error:
                failed += 1
                errors.append(RowError(item.row.line, str(error)))
        job = ImportJob(
            entity=entity,
            filename=filename[:200] or "import.csv",
            created_by=principal.user_id,
            owner_user_id=owner,
            total=len(preview.rows),
            created=created,
            duplicates=preview.count(RowStatus.DUPLICATE),
            invalid=preview.count(RowStatus.INVALID),
            failed=failed,
            errors=tuple(errors[:MAX_REPORTED_ERRORS]),
        )
        with self._uow:
            self._jobs.add(job)
            self._audit.record(
                AuditEntry(
                    actor_user_id=principal.user_id,
                    action="import.completed",
                    resource_type="import_job",
                    resource_id=str(job.id),
                )
            )
        return job

    def recent(self, principal: Principal, limit: int = 20) -> list[ImportJob]:
        self._policy.require(principal, Permission.CUSTOMERS_VIEW)
        return self._jobs.recent(limit)
