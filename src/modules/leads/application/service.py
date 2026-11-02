"""Lead use cases. Authorization (RBAC + ownership scope) is enforced here for
every operation, independently of the HTTP layer."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from modules.access_control.domain.rbac import (
    AccessPolicy,
    Permission,
    Principal,
    ResourceOwnership,
    Scope,
    check_assignable,
)
from modules.audit.application.ports import AuditLog, AuditTrail
from modules.audit.domain.entry import AuditEntry
from modules.leads.application.ports import (
    LEAD_ORDERING,
    CustomerCreation,
    DuplicateCandidate,
    DuplicateFinder,
    LeadFilter,
    LeadRepository,
    LeadVisibility,
    MemberDirectory,
)
from modules.leads.domain.lead import Lead, LeadSource, LeadStatus
from platform_core.application.ports import EventOutbox
from shared.application.ports import UnitOfWork
from shared.domain.errors import ConflictError, DomainValidationError, NotFoundError

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
        duplicates: DuplicateFinder | None = None,
        members: MemberDirectory | None = None,
        trail: AuditTrail | None = None,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._members = members
        self._trail = trail
        self._repo = repository
        self._uow = uow
        self._outbox = outbox
        self._audit = audit
        self._customers = customers
        self._duplicates = duplicates
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
        self,
        principal: Principal,
        *,
        status: LeadStatus | None = None,
        limit: int,
        offset: int,
        filters: LeadFilter | None = None,
    ) -> LeadPage:
        scope = self._policy.require(principal, Permission.LEADS_VIEW)
        visibility = self._visibility(principal, scope)
        filters = filters or LeadFilter(status=status)
        if filters.ordering and filters.ordering.lstrip("-") not in LEAD_ORDERING:
            raise DomainValidationError(
                f"ordering must be one of {sorted(LEAD_ORDERING)} (optionally with '-')."
            )
        return LeadPage(
            items=self._repo.list_visible(visibility, filters, limit=limit, offset=offset),
            total=self._repo.count(visibility, filters),
        )

    def history(self, principal: Principal, lead_id: UUID) -> list[AuditEntry]:
        """Audited changes of the lead (field names only), newest first; kept after conversion."""
        self.get(principal, lead_id)
        return [] if self._trail is None else self._trail.entries_for(AGGREGATE, str(lead_id))

    def assign(self, principal: Principal, lead_id: UUID, owner_user_id: UUID) -> Lead:
        """Hand a lead to another member: a manager cannot, a head of sales within their
        department, a director to any active member of the company."""
        with self._uow:
            lead = self.get(principal, lead_id)
            scope = self._policy.require(principal, Permission.LEADS_UPDATE, _ownership(lead))
            member = (
                self._members.active_member(owner_user_id)
                if self._members is not None and owner_user_id != principal.user_id
                else None
            )
            check_assignable(principal, scope, owner_user_id, member, what="leads")
            team_id = member.department_id if member is not None else principal.department_id
            version = lead.version
            if lead.assign(owner_user_id, team_id):
                self._repo.save(lead, expected_version=version)
                self._record(principal, "lead.reassigned", lead, ("owner",))
        return lead

    def find_duplicates(self, principal: Principal, lead_id: UUID) -> list[DuplicateCandidate]:
        """Candidate duplicates (other leads and existing customers) that share this
        lead's e-mail or phone. No automatic merge; the caller decides."""
        lead = self.get(principal, lead_id)  # 404 if not visible (LEADS_VIEW)
        if self._duplicates is None:
            return []
        email = str(lead.email) if lead.email else None
        phone_digits = re.sub(r"\D", "", str(lead.phone)) if lead.phone else None
        if not email and not phone_digits:
            return []
        return self._duplicates.find(
            exclude_lead_id=lead_id, email=email, phone_digits=phone_digits
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
            before = (lead.name, lead.email, lead.phone)
            if lead.update_details(name=command.name, email=command.email, phone=command.phone):
                self._repo.save(lead, expected_version=version)
                changed = tuple(
                    name
                    for name, old, new in zip(
                        ("name", "email", "phone"),
                        before,
                        (lead.name, lead.email, lead.phone),
                        strict=True,
                    )
                    if old != new
                )
                self._record(principal, "lead.updated", lead, changed)
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

    def convert(
        self, principal: Principal, lead_id: UUID, customer_id: UUID | None = None
    ) -> ConversionResult:
        """Convert a qualified lead into a NEW customer, or link it to an EXISTING one
        (``customer_id``: a confirmed duplicate, a returning client) without creating a
        second card. Idempotent: a lead already converted returns its customer and does
        nothing else. The lead and its history stay (status ``converted``).

        Writes go through the customers context on the same tenant database, so the
        customer and the converted lead commit together (nested transaction)."""
        with self._uow:
            lead = self._load_for_write(principal, lead_id, Permission.LEADS_CONVERT)
            if lead.status is LeadStatus.CONVERTED and lead.converted_customer_id is not None:
                return ConversionResult(lead, lead.converted_customer_id, already_converted=True)
            version = lead.version
            if customer_id is not None:
                customer_id = self._customers.existing(principal, customer_id)
            else:
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

    def _record(
        self, principal: Principal, action: str, lead: Lead, fields: tuple[str, ...] = ()
    ) -> None:
        self._audit.record(
            AuditEntry(
                actor_user_id=principal.user_id,
                action=action,
                resource_type=AGGREGATE,
                resource_id=str(lead.id),
                changed_fields=fields,
            )
        )
