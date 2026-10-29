"""Customer use cases. Authorization (RBAC + ownership scope) is enforced here,
for every operation, independently of the HTTP layer."""

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
from modules.customers.application.ports import CustomerRepository, CustomerVisibility
from modules.customers.domain.customer import Customer
from platform_core.application.ports import EventOutbox
from shared.application.ports import UnitOfWork
from shared.domain.errors import ConflictError, NotFoundError

AGGREGATE = "customer"


@dataclass(frozen=True, slots=True)
class CreateCustomer:
    name: str
    email: str | None = None
    phone: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateCustomer:
    customer_id: UUID
    expected_version: int | None = None
    name: str | None = None
    email: str | None = None
    phone: str | None = None


@dataclass(frozen=True, slots=True)
class CustomerPage:
    items: list[Customer]
    total: int


def _ownership(customer: Customer) -> ResourceOwnership:
    return ResourceOwnership(owner_user_id=customer.owner_user_id, team_id=customer.team_id)


class CustomerService:
    def __init__(
        self,
        repository: CustomerRepository,
        uow: UnitOfWork,
        outbox: EventOutbox,
        audit: AuditLog,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._repo = repository
        self._uow = uow
        self._outbox = outbox
        self._audit = audit
        self._policy = policy or AccessPolicy()

    def create(self, principal: Principal, command: CreateCustomer) -> Customer:
        self._policy.require(principal, Permission.CUSTOMERS_CREATE)
        customer, event = Customer.register(
            name=command.name,
            owner_user_id=principal.user_id,
            team_id=principal.department_id,
            email=command.email,
            phone=command.phone,
        )
        with self._uow:
            self._repo.add(customer)
            self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(customer.id))
            self._audit.record(
                AuditEntry(
                    actor_user_id=principal.user_id,
                    action="customer.created",
                    resource_type=AGGREGATE,
                    resource_id=str(customer.id),
                )
            )
        return customer

    def get(self, principal: Principal, customer_id: UUID) -> Customer:
        customer = self._repo.get(customer_id)
        # Invisible and non-existent customers are indistinguishable (404).
        if customer is None or not self._policy.allows(
            principal, Permission.CUSTOMERS_VIEW, _ownership(customer)
        ):
            raise NotFoundError("Customer not found.")
        return customer

    def list_customers(self, principal: Principal, *, limit: int, offset: int) -> CustomerPage:
        scope = self._policy.require(principal, Permission.CUSTOMERS_VIEW)
        if scope is Scope.ALL:
            visibility = CustomerVisibility()
        elif scope is Scope.TEAM:
            visibility = CustomerVisibility(
                owner_user_id=principal.user_id, team_ids=principal.team_ids
            )
        else:
            visibility = CustomerVisibility(owner_user_id=principal.user_id)
        return CustomerPage(
            items=self._repo.list_visible(visibility, limit=limit, offset=offset),
            total=self._repo.count(visibility),
        )

    def update(self, principal: Principal, command: UpdateCustomer) -> Customer:
        with self._uow:
            customer = self.get(principal, command.customer_id)
            self._policy.require(principal, Permission.CUSTOMERS_UPDATE, _ownership(customer))
            current_version = customer.version
            if command.expected_version is not None and command.expected_version != current_version:
                raise ConflictError("Customer was modified by someone else; reload and retry.")
            event = customer.update_details(
                name=command.name, email=command.email, phone=command.phone
            )
            if event is None:
                return customer
            self._repo.save(customer, expected_version=current_version)
            self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(customer.id))
            self._audit.record(
                AuditEntry(
                    actor_user_id=principal.user_id,
                    action="customer.updated",
                    resource_type=AGGREGATE,
                    resource_id=str(customer.id),
                    changed_fields=event.changed_fields,
                )
            )
        return customer
