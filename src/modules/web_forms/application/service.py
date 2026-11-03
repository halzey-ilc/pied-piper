"""Website lead forms: managing intake keys and receiving public submissions.

Who manages keys: the director (staff.manage with company scope) — any department; the
head of sales (team scope) — only their own department. Managers and other roles cannot.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal, Scope
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.web_forms.application.ports import (
    Departments,
    DuplicateGuard,
    IntakeOwners,
    KeyStore,
    LeadIntake,
)
from modules.web_forms.domain.key import (
    MAX_ACTIVE_KEYS_PER_DEPARTMENT,
    IntakeKey,
    WebsiteSubmission,
    clean_key_name,
    is_token_shaped,
)
from shared.application.ports import UnitOfWork
from shared.domain.errors import (
    AccessDeniedError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)

AGGREGATE = "web_form_key"


def _new_token() -> str:
    return secrets.token_urlsafe(24)  # 32 URL-safe characters, 192 bits


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class CreateKey:
    name: str
    department_id: UUID | None = None


class WebFormKeyService:
    def __init__(
        self,
        store: KeyStore,
        departments: Departments,
        audit: AuditLog,
        uow: UnitOfWork,
        *,
        policy: AccessPolicy | None = None,
        new_token: Callable[[], str] = _new_token,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._store = store
        self._departments = departments
        self._audit = audit
        self._uow = uow
        self._policy = policy or AccessPolicy()
        self._new_token = new_token
        self._clock = clock

    def list_keys(self, principal: Principal, department_id: UUID | None) -> list[IntakeKey]:
        scope = self._policy.require(principal, Permission.STAFF_MANAGE)
        if scope is Scope.ALL:
            return self._store.list(principal.tenant_id, department_id)
        return self._store.list(principal.tenant_id, self._own_department(principal, department_id))

    def create(self, principal: Principal, command: CreateKey) -> IntakeKey:
        department_id = self._department_for_write(principal, command.department_id)
        name = clean_key_name(command.name)
        if self._store.count_active(principal.tenant_id, department_id) >= (
            MAX_ACTIVE_KEYS_PER_DEPARTMENT
        ):
            raise ConflictError(
                f"A department can have at most {MAX_ACTIVE_KEYS_PER_DEPARTMENT} active keys; "
                "revoke an unused one first."
            )
        key = IntakeKey(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            department_id=department_id,
            name=name,
            token=self._new_token(),
            created_by=principal.user_id,
            created_at=self._clock(),
        )
        with self._uow:
            self._store.add(key)
            self._record(principal, "web_form_key.created", key)
        return key

    def revoke(self, principal: Principal, key_id: UUID) -> IntakeKey:
        """Idempotent: revoking a revoked key returns it unchanged."""
        scope = self._policy.require(principal, Permission.STAFF_MANAGE)
        key = self._store.get(principal.tenant_id, key_id)
        if key is None:
            raise NotFoundError("Key not found.")
        if scope is not Scope.ALL and key.department_id != principal.department_id:
            raise NotFoundError("Key not found.")  # another department's keys are invisible
        if not key.is_active:
            return key
        with self._uow:
            revoked = self._store.revoke(key.id, at=self._clock(), by=principal.user_id)
            self._record(principal, "web_form_key.revoked", revoked)
        return revoked

    # --- helpers ------------------------------------------------------------

    def _own_department(self, principal: Principal, department_id: UUID | None) -> UUID:
        own = principal.department_id
        if own is None or (department_id is not None and department_id != own):
            raise AccessDeniedError("You can manage website forms of your own department only.")
        return own

    def _department_for_write(self, principal: Principal, department_id: UUID | None) -> UUID:
        scope = self._policy.require(principal, Permission.STAFF_MANAGE)
        if scope is not Scope.ALL:
            return self._own_department(principal, department_id)
        if department_id is None:
            raise DomainValidationError("department_id is required.")
        if not self._departments.exists(principal.tenant_id, department_id):
            raise NotFoundError("Department not found.")
        return department_id

    def _record(self, principal: Principal, action: str, key: IntakeKey) -> None:
        self._audit.record(
            AuditEntry(
                actor_user_id=principal.user_id,
                action=action,
                resource_type=AGGREGATE,
                resource_id=str(key.id),
                details={"department_id": str(key.department_id)},
            )
        )


class UnknownKeyError(NotFoundError):
    """Unknown, malformed or revoked token. Always the same answer: no enumeration."""

    def __init__(self) -> None:
        super().__init__("Not found.")


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    lead_id: UUID | None  # None: a repeated submission, nothing new was created
    duplicate: bool


def resolve_key(store: KeyStore, token: str) -> IntakeKey:
    """Control-plane step of a public submission: which company and department."""
    key = store.find_active(token) if is_token_shaped(token) else None
    if key is None:
        raise UnknownKeyError
    return key


class WebsiteLeadIntake:
    """Tenant step of a public submission; runs inside the key's tenant context."""

    def __init__(
        self,
        store: KeyStore,
        owners: IntakeOwners,
        intake: LeadIntake,
        duplicates: DuplicateGuard,
        *,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._store = store
        self._owners = owners
        self._intake = intake
        self._duplicates = duplicates
        self._clock = clock

    def submit(self, key: IntakeKey, submission: WebsiteSubmission) -> SubmissionResult:
        principal = self._owners.principal_for(key)
        if principal is None:
            raise UnknownKeyError  # nobody left in the company to receive leads
        principal = replace(principal, department_id=key.department_id)
        fingerprint = submission.fingerprint()
        if not self._duplicates.claim(key.id, fingerprint):
            return SubmissionResult(lead_id=None, duplicate=True)
        try:
            lead_id = self._intake.capture(principal, key, submission)
        except BaseException:
            self._duplicates.release(key.id, fingerprint)  # a failed attempt may be retried
            raise
        self._store.mark_used(key.id, at=self._clock())
        return SubmissionResult(lead_id=lead_id, duplicate=False)
