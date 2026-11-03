"""Wires the customer use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from modules.audit.domain.entry import AuditEntry
from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.customers.application.service import CustomerService
from modules.customers.infrastructure.repository import DjangoCustomerRepository
from modules.organizations.infrastructure.memberships import DjangoMemberDirectory
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from shared.infrastructure.unit_of_work import DjangoUnitOfWork

if TYPE_CHECKING:
    from modules.customers.application.merge import CustomerMerger


def customer_service() -> CustomerService:
    tenant = require_tenant()  # fails closed without a tenant context
    alias = tenant.db_alias
    return CustomerService(
        repository=DjangoCustomerRepository(alias),
        uow=DjangoUnitOfWork(alias),
        outbox=DjangoEventOutbox(alias),
        audit=DjangoAuditLog(alias),
        members=DjangoMemberDirectory(tenant.tenant_id),
        trail=DjangoAuditLog(alias),
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
                    details={"from_owner": str(from_user), "to_owner": str(to_user)},
                )
            )
    return len(moved)


def customer_id_by_phone(phone: str) -> UUID | None:
    """System lookup for automations (e.g. a missed call): the one active customer with this
    E.164 phone, or None when there is none or the match is ambiguous."""
    from modules.customers.infrastructure.models import CustomerRecord

    ids = list(
        CustomerRecord.objects.using(require_tenant().db_alias)
        .filter(phone=phone, archived_at__isnull=True)
        .values_list("id", flat=True)[:2]
    )
    return ids[0] if len(ids) == 1 else None


def customer_merger() -> CustomerMerger:
    """Merging duplicate customer cards (spec 2.10) on the active tenant database."""
    from modules.customers.application.merge import CustomerMerger
    from modules.customers.infrastructure.references import DjangoCustomerReferences

    alias = require_tenant().db_alias
    return CustomerMerger(
        repository=DjangoCustomerRepository(alias),
        references=DjangoCustomerReferences(alias),
        load=customer_service().get,
        uow=DjangoUnitOfWork(alias),
        audit=DjangoAuditLog(alias),
    )
