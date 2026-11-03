"""Adapters of the import use case, bound to the active tenant database."""

from __future__ import annotations

from uuid import UUID

from django.db.models.functions import Lower

from modules.access_control.domain.rbac import MemberRef, Principal
from modules.activities.infrastructure.next_steps import ActivityNextSteps
from modules.customers.application.service import CreateCustomer
from modules.customers.composition import customer_service
from modules.customers.infrastructure.models import CustomerRecord
from modules.imports.application.ports import (
    CreatedDeal,
    DealDraft,
    ExistingMatch,
    ImportJob,
    RowError,
)
from modules.imports.domain.rows import ImportEntity, ImportRow
from modules.imports.infrastructure.models import ImportJobRecord
from modules.leads.application.service import CaptureLead
from modules.leads.composition import lead_service
from modules.leads.domain.lead import LeadSource
from modules.leads.infrastructure.models import LeadRecord
from modules.organizations.composition import company_settings
from modules.organizations.infrastructure.memberships import DjangoMemberDirectory
from modules.organizations.infrastructure.models import Membership
from modules.sales_pipeline.application.deal_service import OpenDeal, StageMoveExtras
from modules.sales_pipeline.composition import deal_service, pipeline_service
from modules.sales_pipeline.domain.loss_reason import LossReason
from modules.sales_pipeline.domain.pipeline import Pipeline
from modules.sales_pipeline.infrastructure.models import DealRecord
from modules.sales_pipeline.infrastructure.repository import (
    DjangoLossReasonRepository,
    DjangoPipelineRepository,
)
from shared.infrastructure.unit_of_work import DjangoUnitOfWork

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
                        found.setdefault(row.phone, _match(kind, row))
            if emails:
                rows = (
                    model.objects.using(self.using)
                    .annotate(email_lower=Lower("email"))
                    .filter(email_lower__in=emails)
                )
                for row in rows:
                    if row.email:
                        found.setdefault(row.email.lower(), _match(kind, row))
        return found


def _match(kind: str, row: CustomerRecord | LeadRecord) -> ExistingMatch:
    if isinstance(row, CustomerRecord):
        return ExistingMatch(
            kind,
            row.id,
            row.name,
            owner_user_id=row.owner_user_id,
            team_id=row.team_id,
            is_archived=row.archived_at is not None,
        )
    return ExistingMatch(kind, row.id, row.name)


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


class DjangoDealReferences:
    """Pipelines, loss reasons, members and deals of the active company."""

    def __init__(self, using: str, tenant_id: UUID) -> None:
        self.using = using
        self.tenant_id = tenant_id
        self._directory = DjangoMemberDirectory(tenant_id)

    def pipelines(self, principal: Principal) -> list[Pipeline]:
        return pipeline_service().list_pipelines(principal)

    def default_pipeline(self, department_id: UUID | None) -> Pipeline | None:
        # The same choice as opening a deal without a pipeline (DealService._resolve_pipeline).
        repository = DjangoPipelineRepository(self.using)
        own = repository.get_default(department_id) if department_id is not None else None
        return own or repository.get_default()

    def loss_reasons(self) -> list[LossReason]:
        return DjangoLossReasonRepository(self.using).list_all(active_only=True)

    def members_by_login(self) -> dict[str, MemberRef]:
        rows = (
            Membership.objects.using("default")
            .filter(
                organization_id=self.tenant_id,
                is_active=True,
                organization__is_active=True,
                user__is_active=True,
            )
            .select_related("user")
            .only("user_id", "department_id", "user__username", "user__email")
        )
        found: dict[str, MemberRef] = {}
        for row in rows:
            ref = MemberRef(user_id=row.user_id, department_id=row.department_id)
            for key in (row.user.username, row.user.email):
                if key:
                    found.setdefault(key.strip().lower(), ref)
        return found

    def member(self, user_id: UUID) -> MemberRef | None:
        return self._directory.active_member(user_id)

    def existing_deals(self, titles: set[str]) -> set[tuple[str, UUID | None]]:
        if not titles:
            return set()
        rows = (
            DealRecord.objects.using(self.using)
            .annotate(title_lower=Lower("title"))
            .filter(title_lower__in=titles)
            .values_list("title_lower", "customer_id")
        )
        return {(title, customer) for title, customer in rows}

    def default_currency(self) -> str:
        return company_settings(self.tenant_id)["currency"]


class ServiceDealCreator:
    """One row = one transaction: the new customer, the deal, its owner and its stage move
    go through the customers and sales_pipeline services (rules, history, events, audit)."""

    def __init__(self, using: str) -> None:
        self.using = using

    def create(self, principal: Principal, draft: DealDraft) -> CreatedDeal:
        owner = draft.owner_user_id
        with DjangoUnitOfWork(self.using):
            customer_id = draft.customer_id
            if customer_id is None and draft.new_customer is not None:
                customers = customer_service()
                contact = draft.new_customer
                customer = customers.create(
                    principal,
                    CreateCustomer(name=contact.name, email=contact.email, phone=contact.phone),
                )
                if owner != principal.user_id:
                    customers.assign(principal, customer.id, owner)
                customer_id = customer.id
            deals = deal_service()
            deal = deals.open_deal(
                principal,
                OpenDeal(
                    title=draft.title,
                    amount=draft.amount,
                    currency=draft.currency,
                    pipeline_id=draft.pipeline_id,
                    customer_id=customer_id,
                    source=draft.source,
                    expected_close_date=draft.expected_close_date,
                ),
            )
            if owner != principal.user_id:
                deals.assign(principal, deal.id, owner)
            if draft.move_to_stage:
                deals.move_stage(
                    principal,
                    deal.id,
                    draft.stage_id,
                    reason=draft.loss_reason,
                    loss_reason_id=draft.loss_reason_id,
                    extras=StageMoveExtras(next_step=draft.next_step),
                )
            elif draft.next_step is not None:
                # A deal left on the first stage: its optional next step is a task as usual.
                ActivityNextSteps().create(
                    principal, "deal", deal.id, draft.next_step, default_assignee=owner
                )
        return CreatedDeal(deal_id=deal.id, customer_id=customer_id)


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
