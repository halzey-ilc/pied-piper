"""Pay terms use cases. The director (company settings permission) sets and lists the terms
of every employee; a member reads only their own earnings (team cabinet «Мой месяц»)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.planning.domain.compensation import Compensation
from shared.application.ports import UnitOfWork
from shared.domain.errors import NotFoundError


class CompensationRepository(Protocol):
    def get(self, user_id: UUID) -> Compensation | None: ...

    def list_all(self) -> list[Compensation]: ...

    def upsert(self, compensation: Compensation) -> None: ...


class CompensationService:
    def __init__(
        self,
        repository: CompensationRepository,
        uow: UnitOfWork,
        audit: AuditLog,
        currency: str,
        is_member: Callable[[UUID], bool],
        policy: AccessPolicy | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repo = repository
        self._uow = uow
        self._audit = audit
        self._currency = currency
        self._is_member = is_member
        self._policy = policy or AccessPolicy()
        self._now = clock

    def list_terms(self, principal: Principal) -> list[Compensation]:
        self._policy.require(principal, Permission.COMPANY_SETTINGS_MANAGE)
        return self._repo.list_all()

    def set_terms(
        self,
        principal: Principal,
        user_id: UUID,
        *,
        base_salary: Decimal,
        commission_percent: Decimal,
    ) -> Compensation:
        self._policy.require(principal, Permission.COMPANY_SETTINGS_MANAGE)
        if not self._is_member(user_id):
            raise NotFoundError("Member not found.")
        terms = Compensation(
            user_id=user_id,
            base_salary=base_salary,
            commission_percent=commission_percent,
            currency=self._currency,
            updated_at=self._now(),
        )
        with self._uow:
            self._repo.upsert(terms)
            self._audit.record(
                AuditEntry(
                    actor_user_id=principal.user_id,
                    action="compensation.set",
                    resource_type="compensation",
                    resource_id=str(user_id),
                )
            )
        return terms
