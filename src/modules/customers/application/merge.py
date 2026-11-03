"""Merging duplicate customer cards (spec 2.10).

The duplicate (source) is merged into the kept card (target): every record that points at the
duplicate — deals, tasks/notes/calls in the timeline, meetings, calls, payments, invoices,
converted leads, conversations — is re-pointed at the target in one transaction. The target
keeps its contacts; only its empty ones are filled from the duplicate. The duplicate is never
deleted: it is archived with a "merged into" marker and both cards get an audit entry.

Allowed to the director (any customer) and a head of sales (customers of their department);
a manager cannot merge.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
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
from modules.customers.application.ports import CustomerRepository
from modules.customers.domain.customer import Customer
from shared.application.ports import UnitOfWork
from shared.domain.errors import AccessDeniedError, ConflictError, DomainValidationError

AGGREGATE = "customer"


class CustomerReferences(Protocol):
    def count(self, customer_id: UUID) -> dict[str, int]: ...

    def move(self, source_id: UUID, target_id: UUID) -> dict[str, int]: ...


@dataclass(frozen=True, slots=True)
class MergePlan:
    source: Customer
    target: Customer
    # How many records of each kind point at the duplicate (moved on merge).
    references: dict[str, int]
    # Target contact fields that will be filled from the duplicate.
    fills: tuple[str, ...]


def _ownership(customer: Customer) -> ResourceOwnership:
    return ResourceOwnership(owner_user_id=customer.owner_user_id, team_id=customer.team_id)


class CustomerMerger:
    def __init__(
        self,
        repository: CustomerRepository,
        references: CustomerReferences,
        load: Callable[[Principal, UUID], Customer],
        uow: UnitOfWork,
        audit: AuditLog,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._repo = repository
        self._refs = references
        self._load = load  # visibility-checked read (404 for customers the caller can't see)
        self._uow = uow
        self._audit = audit
        self._policy = policy or AccessPolicy()

    def _check(self, principal: Principal, source_id: UUID, target_id: UUID) -> MergePlan:
        if source_id == target_id:
            raise DomainValidationError("Choose two different customers to merge.")
        source = self._load(principal, source_id)
        target = self._load(principal, target_id)
        for customer in (source, target):
            scope = self._policy.require(
                principal, Permission.CUSTOMERS_UPDATE, _ownership(customer)
            )
            if scope is Scope.OWN:
                raise AccessDeniedError("Merging customers is for the director or a head of sales.")
        if source.merged_into_id is not None:
            raise ConflictError("This customer has already been merged into another one.")
        if target.merged_into_id is not None or target.is_archived:
            raise ConflictError("Merge into an active customer: restore the kept card first.")
        fills = tuple(
            name
            for name, empty, available in (
                ("email", target.email is None, source.email is not None),
                ("phone", target.phone is None, source.phone is not None),
            )
            if empty and available
        )
        return MergePlan(
            source=source,
            target=target,
            references=self._refs.count(source.id),
            fills=fills,
        )

    def preview(self, principal: Principal, source_id: UUID, target_id: UUID) -> MergePlan:
        return self._check(principal, source_id, target_id)

    def merge(self, principal: Principal, source_id: UUID, target_id: UUID) -> MergePlan:
        with self._uow:
            plan = self._check(principal, source_id, target_id)
            source, target = plan.source, plan.target
            source_version, target_version = source.version, target.version
            email, phone = source.email, source.phone
            # The duplicate releases its e-mail first: e-mails are unique per company.
            source.mark_merged_into(target.id, release_email="email" in plan.fills)
            self._repo.save(source, expected_version=source_version)
            if target.absorb_contacts(email, phone):
                self._repo.save(target, expected_version=target_version)
            moved = self._refs.move(source.id, target.id)
            summary = tuple(f"{label}:{count}" for label, count in moved.items() if count)
            self._audit.record(
                AuditEntry(
                    actor_user_id=principal.user_id,
                    action="customer.merged_into",
                    resource_type=AGGREGATE,
                    resource_id=str(source.id),
                    changed_fields=("merged_into", *summary),
                )
            )
            self._audit.record(
                AuditEntry(
                    actor_user_id=principal.user_id,
                    action="customer.merged",
                    resource_type=AGGREGATE,
                    resource_id=str(target.id),
                    changed_fields=(*plan.fills, *summary),
                )
            )
        return MergePlan(source=source, target=target, references=moved, fills=plan.fills)
