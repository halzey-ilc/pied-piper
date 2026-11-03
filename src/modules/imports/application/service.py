"""Import of leads, customers or deals from a CSV file: preview first, then commit.

Duplicates are never created: a row whose phone or e-mail already belongs to a record of
the company (or appears earlier in the same file) is skipped and reported; a deal is a
duplicate when the company already has a deal with that title for the same customer. Every
created record goes through its module's service, so ownership, events and audit apply as
for a record created by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.imports.application.deals import ROW_ERROR_MESSAGES, DealPlan, DealRowPlanner
from modules.imports.application.ports import (
    DealCreator,
    DealReferences,
    ExistingContacts,
    ExistingMatch,
    ImportJob,
    ImportJobStore,
    RecordCreator,
    RowError,
)
from modules.imports.domain.rows import (
    DealImportRow,
    ImportEntity,
    ImportRow,
    parse_deal_rows,
    parse_rows,
)
from shared.application.ports import UnitOfWork
from shared.domain.errors import DomainError

MAX_REPORTED_ERRORS = 50

_CREATE_PERMISSION = {
    ImportEntity.LEADS: Permission.LEADS_CREATE,
    ImportEntity.CUSTOMERS: Permission.CUSTOMERS_CREATE,
    ImportEntity.DEALS: Permission.DEALS_CREATE,
}


class RowStatus(StrEnum):
    NEW = "new"
    DUPLICATE = "duplicate"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class PreviewRow:
    row: ImportRow | DealImportRow
    status: RowStatus
    reason: str | None = None
    duplicate_of: ExistingMatch | None = None
    duplicate_line: int | None = None
    deal: DealPlan | None = None  # deals: what will be created


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
        deal_creator: DealCreator | None = None,
        deal_refs: DealReferences | None = None,
    ) -> None:
        self._deal_creator = deal_creator
        self._deal_refs = deal_refs
        self._contacts = contacts
        self._creator = creator
        self._jobs = jobs
        self._audit = audit
        self._uow = uow
        self._policy = policy or AccessPolicy()

    def preview(
        self,
        principal: Principal,
        entity: ImportEntity,
        text: str,
        owner_user_id: UUID | None = None,
    ) -> Preview:
        self._policy.require(principal, _CREATE_PERMISSION[entity])
        if entity is ImportEntity.DEALS:
            return self._preview_deals(principal, text, owner_user_id or principal.user_id)
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
        preview = self.preview(principal, entity, text, owner_user_id)
        owner = owner_user_id or principal.user_id
        if entity is ImportEntity.DEALS:
            created, failed, errors = self._commit_deals(principal, preview)
        else:
            created, failed, errors = self._commit_records(principal, entity, preview, owner)
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

    def _commit_records(
        self, principal: Principal, entity: ImportEntity, preview: Preview, owner: UUID
    ) -> tuple[int, int, list[RowError]]:
        created = failed = 0
        errors: list[RowError] = []
        for item in preview.rows:
            if item.status is not RowStatus.NEW:
                if item.status is RowStatus.INVALID:
                    errors.append(RowError(item.row.line, item.reason or "Ошибка в строке"))
                continue
            if not isinstance(item.row, ImportRow):
                continue
            try:
                self._creator.create(principal, entity, item.row, owner)
                created += 1
            except DomainError as error:
                failed += 1
                errors.append(RowError(item.row.line, str(error)))
        return created, failed, errors

    def _preview_deals(self, principal: Principal, text: str, default_owner: UUID) -> Preview:
        if self._deal_refs is None:
            raise DomainError("Deal import is not configured.")
        planner = DealRowPlanner(
            principal, self._deal_refs, self._contacts, self._policy, default_owner
        )
        result: list[PreviewRow] = []
        for planned in planner.plan(parse_deal_rows(text)):
            if planned.errors:
                status, reason = RowStatus.INVALID, "; ".join(planned.errors)
            elif planned.duplicate_reason:
                status, reason = RowStatus.DUPLICATE, planned.duplicate_reason
            else:
                status, reason = RowStatus.NEW, None
            result.append(
                PreviewRow(
                    planned.row,
                    status,
                    reason,
                    duplicate_of=planned.duplicate_of,
                    duplicate_line=planned.duplicate_line,
                    deal=planned.plan,
                )
            )
        return Preview(ImportEntity.DEALS, result)

    def _commit_deals(
        self, principal: Principal, preview: Preview
    ) -> tuple[int, int, list[RowError]]:
        """Each row in its own transaction (inside the creator); a customer created for one
        row is reused by later rows with the same phone or e-mail."""
        if self._deal_creator is None:
            raise DomainError("Deal import is not configured.")
        created = failed = 0
        errors: list[RowError] = []
        new_customers: dict[str, UUID] = {}
        for item in preview.rows:
            if item.status is RowStatus.INVALID:
                errors.append(RowError(item.row.line, item.reason or "Ошибка в строке"))
            if item.status is not RowStatus.NEW or item.deal is None:
                continue
            draft = item.deal.draft
            contact = draft.new_customer
            keys = [k for k in (contact.phone, contact.email) if k] if contact else []
            known = next((new_customers[k] for k in keys if k in new_customers), None)
            if known is not None:
                draft = replace(draft, customer_id=known, new_customer=None)
            try:
                result = self._deal_creator.create(principal, draft)
            except DomainError as error:
                failed += 1
                message = ROW_ERROR_MESSAGES.get(error.code, str(error))
                errors.append(RowError(item.row.line, message))
                continue
            created += 1
            if result.customer_id is not None:
                for key in keys:
                    new_customers.setdefault(key, result.customer_id)
        return created, failed, errors

    def recent(self, principal: Principal, limit: int = 20) -> list[ImportJob]:
        self._policy.require(principal, Permission.CUSTOMERS_VIEW)
        return self._jobs.recent(limit)
