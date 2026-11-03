"""Wires the customer use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from uuid import UUID

from modules.audit.domain.entry import AuditEntry
from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.customers.application.service import CustomerService
from modules.customers.infrastructure.repository import DjangoCustomerRepository
from modules.organizations.infrastructure.memberships import DjangoMemberDirectory
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def customer_service() -> CustomerService:
    tenant = require_tenant()  # fails closed without a tenant context
    alias = tenant.db_alias
    return CustomerService(
        repository=DjangoCustomerRepository(alias),
        uow=DjangoUnitOfWork(alias),
        outbox=DjangoEventOutbox(alias),
        audit=DjangoAuditLog(alias),
        members=DjangoMemberDirectory(tenant.tenant_id),
    )


def hand_over_customers(from_user: UUID, to_user: UUID, to_team: UUID | None, actor: UUID) -> int:
    """Staff offboarding: the active customers of a deactivated member go to ``to_user``; each
    move is audited as ``customer.reassigned``. Archived customers keep their owner."""
    alias = require_tenant().db_alias
    with DjangoUnitOfWork(alias):
        moved = DjangoCustomerRepository(alias).hand_over_customers(from_user, to_user, to_team)
        audit = DjangoAuditLog(alias)
        for record_id in moved:
            audit.record(
                AuditEntry(
                    actor_user_id=actor,
                    action="customer.reassigned",
                    resource_type="customer",
                    resource_id=str(record_id),
                    changed_fields=("owner",),
                )
            )
    return len(moved)
