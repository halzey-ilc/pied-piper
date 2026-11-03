"""Adapters of the import use case, bound to the active tenant database."""

from __future__ import annotations

from uuid import UUID

from django.db.models.functions import Lower

from modules.access_control.domain.rbac import Principal
from modules.customers.application.service import CreateCustomer
from modules.customers.composition import customer_service
from modules.customers.infrastructure.models import CustomerRecord
from modules.imports.application.ports import ExistingMatch, ImportJob, RowError
from modules.imports.domain.rows import ImportEntity, ImportRow
from modules.imports.infrastructure.models import ImportJobRecord
from modules.leads.application.service import CaptureLead
from modules.leads.composition import lead_service
from modules.leads.domain.lead import LeadSource
from modules.leads.infrastructure.models import LeadRecord

# Russian and English names of lead sources in a file; anything else is "import"
_SOURCES: dict[str, LeadSource] = {
    "сайт": LeadSource.WEBSITE,
    "website": LeadSource.WEBSITE,
    "рекомендация": LeadSource.REFERRAL,
    "referral": LeadSource.REFERRAL,
    "whatsapp": LeadSource.WHATSAPP,
    "ватсап": LeadSource.WHATSAPP,
    "telegram": LeadSource.TELEGRAM,
    "телеграм": LeadSource.TELEGRAM,
    "instagram": LeadSource.INSTAGRAM,
    "инстаграм": LeadSource.INSTAGRAM,
    "звонок": LeadSource.PHONE,
    "телефон": LeadSource.PHONE,
    "phone": LeadSource.PHONE,
    "другое": LeadSource.OTHER,
    "other": LeadSource.OTHER,
}


def lead_source(raw: str | None) -> LeadSource:
    return _SOURCES.get((raw or "").strip().lower(), LeadSource.IMPORT)


class DjangoExistingContacts:
    def __init__(self, using: str) -> None:
        self.using = using

    def find(
        self, entity: ImportEntity, *, phones: set[str], emails: set[str]
    ) -> dict[str, ExistingMatch]:
        found: dict[str, ExistingMatch] = {}
        sources: list[tuple[str, type[CustomerRecord] | type[LeadRecord]]] = [
            ("customer", CustomerRecord)
        ]
        if entity is ImportEntity.LEADS:
            sources.append(("lead", LeadRecord))
        for kind, model in sources:
            if phones:
                for row in model.objects.using(self.using).filter(phone__in=phones):
                    if row.phone:
                        found.setdefault(row.phone, ExistingMatch(kind, row.id, row.name))
            if emails:
                rows = (
                    model.objects.using(self.using)
                    .annotate(email_lower=Lower("email"))
                    .filter(email_lower__in=emails)
                )
                for row in rows:
                    if row.email:
                        found.setdefault(row.email.lower(), ExistingMatch(kind, row.id, row.name))
        return found


class ServiceRecordCreator:
    def create(
        self, principal: Principal, entity: ImportEntity, row: ImportRow, owner_user_id: UUID
    ) -> UUID:
        if entity is ImportEntity.LEADS:
            leads = lead_service()
            lead = leads.capture(
                principal,
                CaptureLead(
                    name=row.name, source=lead_source(row.source), email=row.email, phone=row.phone
                ),
            )
            if owner_user_id != principal.user_id:
                leads.assign(principal, lead.id, owner_user_id)
            return lead.id
        customers = customer_service()
        customer = customers.create(
            principal, CreateCustomer(name=row.name, email=row.email, phone=row.phone)
        )
        if owner_user_id != principal.user_id:
            customers.assign(principal, customer.id, owner_user_id)
        return customer.id


def _job(record: ImportJobRecord) -> ImportJob:
    return ImportJob(
        id=record.id,
        entity=ImportEntity(record.entity),
        filename=record.filename,
        created_by=record.created_by,
        owner_user_id=record.owner_user_id,
        total=record.total,
        created=record.created,
        duplicates=record.duplicates,
        invalid=record.invalid,
        failed=record.failed,
        errors=tuple(RowError(int(e["line"]), str(e["message"])) for e in record.errors),
        created_at=record.created_at,
    )


class DjangoImportJobStore:
    def __init__(self, using: str) -> None:
        self.using = using

    def add(self, job: ImportJob) -> None:
        ImportJobRecord.objects.using(self.using).create(
            id=job.id,
            entity=job.entity.value,
            filename=job.filename,
            created_by=job.created_by,
            owner_user_id=job.owner_user_id,
            total=job.total,
            created=job.created,
            duplicates=job.duplicates,
            invalid=job.invalid,
            failed=job.failed,
            errors=[{"line": e.line, "message": e.message} for e in job.errors],
            created_at=job.created_at,
        )

    def recent(self, limit: int) -> list[ImportJob]:
        rows = ImportJobRecord.objects.using(self.using).order_by("-created_at")[:limit]
        return [_job(row) for row in rows]
