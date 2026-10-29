"""Invoice use cases. RBAC + ownership scope enforced here for every operation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
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
from modules.payments.application.ports import (
    CustomerLookup,
    DealLookup,
    InvoiceFilter,
    InvoiceRepository,
    InvoiceVisibility,
)
from modules.payments.domain.invoice import Invoice
from platform_core.application.ports import EventOutbox
from shared.application.ports import UnitOfWork
from shared.domain.errors import NotFoundError
from shared.domain.events import DomainEvent
from shared.domain.value_objects import Money

AGGREGATE = "invoice"
_EventsAction = Callable[["Invoice"], list["DomainEvent"]]


@dataclass(frozen=True, slots=True)
class CreateInvoice:
    amount: Decimal
    currency: str = "USD"
    number: str | None = None
    customer_id: UUID | None = None
    deal_id: UUID | None = None
    due_date: date | None = None


@dataclass(frozen=True, slots=True)
class InvoicePage:
    items: list[Invoice]
    total: int


def _ownership(invoice: Invoice) -> ResourceOwnership:
    return ResourceOwnership(owner_user_id=invoice.owner_user_id, team_id=invoice.team_id)


class InvoiceService:
    def __init__(
        self,
        repository: InvoiceRepository,
        customers: CustomerLookup,
        deals: DealLookup,
        uow: UnitOfWork,
        outbox: EventOutbox,
        audit: AuditLog,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._repo = repository
        self._customers = customers
        self._deals = deals
        self._uow = uow
        self._outbox = outbox
        self._audit = audit
        self._policy = policy or AccessPolicy()

    def get(self, principal: Principal, invoice_id: UUID) -> Invoice:
        invoice = self._repo.get(invoice_id)
        if invoice is None or not self._policy.allows(
            principal, Permission.PAYMENTS_VIEW, _ownership(invoice)
        ):
            raise NotFoundError("Invoice not found.")
        return invoice

    def list_invoices(
        self, principal: Principal, filters: InvoiceFilter, *, limit: int, offset: int
    ) -> InvoicePage:
        scope = self._policy.require(principal, Permission.PAYMENTS_VIEW)
        visibility = self._visibility(principal, scope)
        return InvoicePage(
            items=self._repo.list_visible(visibility, filters, limit=limit, offset=offset),
            total=self._repo.count(visibility, filters),
        )

    def _visibility(self, principal: Principal, scope: Scope) -> InvoiceVisibility:
        if scope is Scope.ALL:
            return InvoiceVisibility()
        if scope is Scope.TEAM:
            return InvoiceVisibility(owner_user_id=principal.user_id, team_ids=principal.team_ids)
        return InvoiceVisibility(owner_user_id=principal.user_id)

    def create(self, principal: Principal, command: CreateInvoice) -> Invoice:
        self._policy.require(principal, Permission.PAYMENTS_CREATE)
        with self._uow:
            if command.customer_id is not None and not self._customers.exists(command.customer_id):
                raise NotFoundError("Linked customer not found.")
            if command.deal_id is not None and not self._deals.exists(command.deal_id):
                raise NotFoundError("Linked deal not found.")
            invoice = Invoice.draft(
                total=Money(command.amount, command.currency),
                owner_user_id=principal.user_id,
                team_id=principal.department_id,
                number=command.number,
                customer_id=command.customer_id,
                deal_id=command.deal_id,
                due_date=command.due_date,
            )
            self._repo.add(invoice)
            self._record(principal, "invoice.created", invoice)
        return invoice

    def issue(self, principal: Principal, invoice_id: UUID) -> Invoice:
        return self._mutate(principal, invoice_id, lambda inv: [inv.issue()], "invoice.issued")

    def void(self, principal: Principal, invoice_id: UUID, reason: str) -> Invoice:
        return self._mutate(principal, invoice_id, lambda inv: [inv.void(reason)], "invoice.voided")

    def record_payment(
        self, principal: Principal, invoice_id: UUID, amount: Decimal, currency: str
    ) -> Invoice:
        def action(invoice: Invoice) -> list[DomainEvent]:
            received, paid = invoice.record_payment(Money(amount, currency))
            return [received] if paid is None else [received, paid]

        return self._mutate(principal, invoice_id, action, "invoice.payment_recorded")

    def _mutate(
        self,
        principal: Principal,
        invoice_id: UUID,
        action: _EventsAction,
        audit_action: str,
    ) -> Invoice:
        with self._uow:
            invoice = self.get(principal, invoice_id)
            self._policy.require(principal, Permission.PAYMENTS_UPDATE, _ownership(invoice))
            version = invoice.version
            events = action(invoice)
            self._repo.save(invoice, expected_version=version)
            for event in events:
                self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(invoice.id))
            self._record(principal, audit_action, invoice)
        return invoice

    def _record(self, principal: Principal, action: str, invoice: Invoice) -> None:
        self._audit.record(
            AuditEntry(
                actor_user_id=principal.user_id,
                action=action,
                resource_type=AGGREGATE,
                resource_id=str(invoice.id),
            )
        )
