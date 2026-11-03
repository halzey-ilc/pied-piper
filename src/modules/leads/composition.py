"""Wires the lead use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from uuid import UUID

from modules.audit.domain.entry import AuditEntry
from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.leads.application.service import LeadService
from modules.leads.infrastructure.customer_creation import CustomersCustomerCreation
from modules.leads.infrastructure.duplicate_finder import DjangoDuplicateFinder
from modules.leads.infrastructure.repository import DjangoLeadRepository
from modules.organizations.infrastructure.memberships import DjangoMemberDirectory
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def lead_service() -> LeadService:
    tenant = require_tenant()  # fails closed without a tenant context
    alias = tenant.db_alias
    return LeadService(
        repository=DjangoLeadRepository(alias),
        uow=DjangoUnitOfWork(alias),
        outbox=DjangoEventOutbox(alias),
        audit=DjangoAuditLog(alias),
        customers=CustomersCustomerCreation(),
        duplicates=DjangoDuplicateFinder(alias),
        members=DjangoMemberDirectory(tenant.tenant_id),
        trail=DjangoAuditLog(alias),
    )


def hand_over_open_leads(from_user: UUID, to_user: UUID, to_team: UUID | None, actor: UUID) -> int:
    """Staff offboarding: the leads a deactivated member was still working (new, qualified) go
    to ``to_user``; each move is audited as ``lead.reassigned`` and shows in the lead's history."""
    alias = require_tenant().db_alias
    with DjangoUnitOfWork(alias):
        moved = DjangoLeadRepository(alias).hand_over_open_leads(from_user, to_user, to_team)
        audit = DjangoAuditLog(alias)
        for record_id in moved:
            audit.record(
                AuditEntry(
                    actor_user_id=actor,
                    action="lead.reassigned",
                    resource_type="lead",
                    resource_id=str(record_id),
                    changed_fields=("owner",),
                )
            )
    return len(moved)
