"""Lead and customer counts (TZ backend §10.2): totals and new records for a period,
for the company, per department or per member — always within the caller's scope.

Leads and customers are separate figures, never summed. "New" = created within the
period (company-local days); "total" = every record that exists now, archived and
converted ones included (``customers_active`` excludes archived customers).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal, Scope
from shared.domain.errors import DomainValidationError


class GroupBy(StrEnum):
    COMPANY = "company"
    DEPARTMENT = "department"
    USER = "user"


@dataclass(frozen=True, slots=True)
class CountScope:
    """Rows the caller may count: ``owner`` alone, or ``owner`` + ``team_ids``; None = all."""

    owner_user_id: UUID | None = None
    team_ids: frozenset[UUID] = frozenset()


@dataclass(frozen=True, slots=True)
class CountRow:
    group_id: UUID | None  # department or user id; None for the company / no department
    leads_total: int
    leads_new: int
    customers_total: int
    customers_active: int
    customers_new: int


class CrmCounter(Protocol):
    def counts(
        self,
        *,
        leads: CountScope,
        customers: CountScope,
        since: datetime,
        until: datetime,
        group_by: GroupBy,
    ) -> list[CountRow]: ...


class CrmCountsService:
    def __init__(self, counter: CrmCounter, policy: AccessPolicy | None = None) -> None:
        self._counter = counter
        self._policy = policy or AccessPolicy()

    def counts(
        self, principal: Principal, *, since: datetime, until: datetime, group_by: GroupBy
    ) -> list[CountRow]:
        if until <= since:
            raise DomainValidationError("The period must end after it starts.")
        return self._counter.counts(
            leads=self._scope(principal, Permission.LEADS_VIEW),
            customers=self._scope(principal, Permission.CUSTOMERS_VIEW),
            since=since,
            until=until,
            group_by=group_by,
        )

    def _scope(self, principal: Principal, permission: Permission) -> CountScope:
        scope = self._policy.require(principal, permission)
        if scope is Scope.ALL:
            return CountScope()
        if scope is Scope.TEAM:
            return CountScope(owner_user_id=principal.user_id, team_ids=principal.team_ids)
        return CountScope(owner_user_id=principal.user_id)
