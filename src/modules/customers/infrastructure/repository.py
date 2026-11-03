"""CustomerRepository adapter.

Justified here because the aggregate is a pure-Python object separate from the
ORM row. Every query goes through an explicit tenant alias.
"""

from __future__ import annotations

import re
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import F, Q, QuerySet

from modules.customers.application.ports import (
    CUSTOMER_ORDERING,
    CustomerFilter,
    CustomerVisibility,
)
from modules.customers.domain.customer import Customer
from modules.customers.infrastructure.models import CustomerRecord
from shared.domain.errors import ConflictError
from shared.domain.value_objects import EmailAddress, PhoneNumber


def _to_domain(row: CustomerRecord) -> Customer:
    return Customer(
        id=row.id,
        name=row.name,
        owner_user_id=row.owner_user_id,
        email=EmailAddress(row.email) if row.email else None,
        phone=PhoneNumber(row.phone) if row.phone else None,
        team_id=row.team_id,
        created_at=row.created_at,
        archived_at=row.archived_at,
        version=row.version,
        merged_into_id=row.merged_into_id,
    )


class DjangoCustomerRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def hand_over_customers(
        self, from_user: UUID, to_user: UUID, team_id: UUID | None
    ) -> list[UUID]:
        """Move a leaving member's active (not archived) customers to another member.
        Archived customers keep their owner as history. Returns the ids of the moved ones."""
        rows = (
            self._qs().select_for_update().filter(owner_user_id=from_user, archived_at__isnull=True)
        )
        ids = list(rows.values_list("id", flat=True))
        if ids:
            changes: dict[str, object] = {"owner_user_id": to_user}
            if team_id is not None:
                changes["team_id"] = team_id
            self._qs().filter(id__in=ids).update(**changes, version=F("version") + 1)
        return ids

    def _qs(self) -> QuerySet[CustomerRecord]:
        return CustomerRecord.objects.using(self.using)

    def _visible(self, visibility: CustomerVisibility) -> QuerySet[CustomerRecord]:
        qs = self._qs()
        if visibility.owner_user_id is None:
            return qs
        condition = Q(owner_user_id=visibility.owner_user_id)
        if visibility.team_ids:
            condition |= Q(team_id__in=visibility.team_ids)
        return qs.filter(condition)

    @staticmethod
    def _search(qs: QuerySet[CustomerRecord], search: str | None) -> QuerySet[CustomerRecord]:
        if not search:
            return qs
        condition = Q(name__icontains=search) | Q(email__icontains=search)
        digits = re.sub(r"\D", "", search)
        if digits:
            condition |= Q(phone__icontains=digits)
        return qs.filter(condition)

    def add(self, customer: Customer) -> None:
        try:
            with transaction.atomic(using=self.using):
                self._qs().create(
                    id=customer.id,
                    name=customer.name,
                    email=str(customer.email) if customer.email else None,
                    phone=str(customer.phone) if customer.phone else None,
                    owner_user_id=customer.owner_user_id,
                    team_id=customer.team_id,
                    version=customer.version,
                    created_at=customer.created_at,
                )
        except IntegrityError:
            raise ConflictError("A customer with this e-mail already exists.") from None

    def get(self, customer_id: UUID) -> Customer | None:
        row = self._qs().filter(id=customer_id).first()
        return None if row is None else _to_domain(row)

    def save(self, customer: Customer, expected_version: int) -> None:
        try:
            with transaction.atomic(using=self.using):
                updated = (
                    self._qs()
                    .filter(id=customer.id, version=expected_version)
                    .update(
                        name=customer.name,
                        email=str(customer.email) if customer.email else None,
                        phone=str(customer.phone) if customer.phone else None,
                        owner_user_id=customer.owner_user_id,
                        team_id=customer.team_id,
                        archived_at=customer.archived_at,
                        merged_into_id=customer.merged_into_id,
                        version=F("version") + 1,
                    )
                )
        except IntegrityError:
            raise ConflictError("A customer with this e-mail already exists.") from None
        if updated != 1:
            raise ConflictError("Customer was modified by someone else; reload and retry.")

    def _filtered(
        self, visibility: CustomerVisibility, filters: CustomerFilter
    ) -> QuerySet[CustomerRecord]:
        qs = self._search(self._visible(visibility), filters.search)
        if filters.owner_user_id is not None:
            qs = qs.filter(owner_user_id=filters.owner_user_id)
        if filters.archived is not None:
            qs = qs.filter(archived_at__isnull=not filters.archived)
        return qs

    def list_visible(
        self, visibility: CustomerVisibility, filters: CustomerFilter, limit: int, offset: int
    ) -> list[Customer]:
        ordering = filters.ordering or "-created_at"
        if ordering.lstrip("-") not in CUSTOMER_ORDERING:
            ordering = "-created_at"
        rows = self._filtered(visibility, filters).order_by(ordering, "id")[offset : offset + limit]
        return [_to_domain(row) for row in rows]

    def count(self, visibility: CustomerVisibility, filters: CustomerFilter) -> int:
        return self._filtered(visibility, filters).count()
