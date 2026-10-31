"""Lead use cases. Authorization (RBAC + ownership scope) is enforced here for
every operation, independently of the HTTP layer."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from modules.access_control.domain.rbac import (
    AccessPolicy,
    Permission,
    Principal,
    ResourceOwnership,
    Scope,
)
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.leads.application.ports import CustomerCreation, LeadRepository, LeadVisibility
from modules.leads.domain.lead import Lead, LeadSource, LeadStatus
from platform_core.application.ports import EventOutbox
from shared.application.ports import UnitOfWork
from shared.domain.errors import ConflictError, NotFoundError

AGGREGATE = "lead"


@dataclass(frozen=True, slots=True)
class CaptureLead:
    name: str
    source: LeadSource = LeadSource.OTHER
    email: str | None = None
    phone: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateLead:
    lead_id: UUID
    expected_version: int | None = None
    name: str | None = None
    email: str | None = None
    phone: str | None = None


@dataclass(frozen=True, slots=True)
class LeadPage:
    items: list[Lead]
    total: int


@dataclass(frozen=True, slots=True)
class ConversionResult:
    lead: Lead
    customer_id: UUID
    already_converted: bool


def _ownership(lead: Lead) -> ResourceOwnership:
    return ResourceOwnership(owner_user_id=lead.owner_user_id, team_id=lead.team_id)


class LeadService:
    def __init__(
        self,
        repository: LeadRepository,
        uow: UnitOfWork,
        outbox: EventOutbox,
        audit: AuditLog,
        customers: CustomerCreation,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._repo = repository
        self._uow = uow
        self._outbox = outbox
        self._audit = audit
        self._customers = customers
        self._policy = policy or AccessPolicy()

    # --- reads --------------------------------------------------------------

    def get(self, principal: Principal, lead_id: UUID) -> Lead:
        lead = self._repo.get(lead_id)
        if lead is None or not self._policy.allows(
            principal, Permission.LEADS_VIEW, _ownership(lead)
        ):
            raise NotFoundError("Lead not found.")
        return lead

    def list_leads(
        self, principal: Principal, *, status: LeadStatus | None, limit: int, offset: int
    ) -> LeadPage:
        scope = self._policy.require(principal, Permission.LEADS_VIEW)
        visibility = self._visibility(principal, scope)
        return LeadPage(
            items=self._repo.list_visible(visibility, status=status, limit=limit, offset=offset),
            total=self._repo.count(visibility, status=status),
        )

    def _visibility(self, principal: Principal, scope: Scope) -> LeadVisibility:
        if scope is Scope.ALL:
            return LeadVisibility()
        if scope is Scope.TEAM:
            return LeadVisibility(owner_user_id=principal.user_id, team_ids=principal.team_ids)
        return LeadVisibility(owner_user_id=principal.user_id)

    # --- writes -------------------------------------------------------------

    def capture(self, principal: Principal, command: CaptureLead) -> Lead:
        self._policy.require(principal, Permission.LEADS_CREATE)
        lead, event = Lead.capture(
            name=command.name,
            owner_user_id=principal.user_id,
            team_id=principal.department_id,
            source=command.source,
            email=command.email,
            phone=command.phone,
        )
        with self._uow:
            self._repo.add(lead)
            self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(lead.id))
            self._record(principal, "lead.captured", lead)
        return lead

    def update(self, principal: Principal, command: UpdateLead) -> Lead:
        with self._uow:
            lead = self._load_for_write(principal, command.lead_id, Permission.LEADS_UPDATE)
            self._check_version(lead, command.expected_version)
            version = lead.version
            if lead.update_details(name=command.name, email=command.email, phone=command.phone):
                self._repo.save(lead, expected_version=version)
                self._record(principal, "lead.updated", lead)
        return lead

    def qualify(self, principal: Principal, lead_id: UUID) -> Lead:
        with self._uow:
            lead = self._load_for_write(principal, lead_id, Permission.LEADS_UPDATE)
            version = lead.version
            event = lead.qualify()
            self._repo.save(lead, expected_version=version)
            self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(lead.id))
            self._record(principal, "lead.qualified", lead)
        return lead

    def disqualify(self, principal: Principal, lead_id: UUID, reason: str) -> Lead:
        with self._uow:
            lead = self._load_for_write(principal, lead_id, Permission.LEADS_UPDATE)
            version = lead.version
            event = lead.disqualify(reason)
            self._repo.save(lead, expected_version=version)
            self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(lead.id))
            self._record(principal, "lead.disqualified", lead)
        return lead

    def convert(self, principal: Principal, lead_id: UUID) -> ConversionResult:
        """Convert a qualified lead into a customer. Idempotent: a lead already
        converted returns its existing customer id without creating a duplicate.

        The customer is created through the customers context on the same tenant
        database, so both writes commit together (nested transaction)."""
        with self._uow:
            lead = self._load_for_write(principal, lead_id, Permission.LEADS_CONVERT)
            if lead.status is LeadStatus.CONVERTED and lead.converted_customer_id is not None:
                return ConversionResult(lead, lead.converted_customer_id, already_converted=True)
            version = lead.version
            customer_id = self._customers.create(
                principal,
                name=lead.name,
                email=str(lead.email) if lead.email else None,
                phone=str(lead.phone) if lead.phone else None,
            )
            event = lead.convert(customer_id)
            self._repo.save(lead, expected_version=version)
            self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(lead.id))
            self._record(principal, "lead.converted", lead)
        return ConversionResult(lead, customer_id, already_converted=False)

    # --- helpers ------------------------------------------------------------

    def _load_for_write(self, principal: Principal, lead_id: UUID, permission: Permission) -> Lead:
        lead = self.get(principal, lead_id)  # 404 if not visible
        self._policy.require(principal, permission, _ownership(lead))
        return lead

    def _check_version(self, lead: Lead, expected_version: int | None) -> None:
        if expected_version is not None and expected_version != lead.version:
            raise ConflictError("Lead was modified by someone else; reload and retry.")

    def _record(self, principal: Principal, action: str, lead: Lead) -> None:
        self._audit.record(
            AuditEntry(
                actor_user_id=principal.user_id,
                action=action,
                resource_type=AGGREGATE,
                resource_id=str(lead.id),
            )
        )
