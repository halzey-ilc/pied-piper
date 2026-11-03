from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from modules.access_control.domain.rbac import Principal
from modules.web_forms.domain.key import IntakeKey, WebsiteSubmission


class KeyStore(Protocol):
    """Intake keys on the control plane (the public request names no company)."""

    def list(self, tenant_id: UUID, department_id: UUID | None) -> list[IntakeKey]: ...

    def get(self, tenant_id: UUID, key_id: UUID) -> IntakeKey | None: ...

    def add(self, key: IntakeKey) -> None: ...

    def revoke(self, key_id: UUID, *, at: datetime, by: UUID) -> IntakeKey: ...

    def count_active(self, tenant_id: UUID, department_id: UUID) -> int: ...

    def find_active(self, token: str) -> IntakeKey | None: ...

    def mark_used(self, key_id: UUID, *, at: datetime) -> None:
        """One more lead received through the key (counter and last use)."""
        ...


class Departments(Protocol):
    def exists(self, tenant_id: UUID, department_id: UUID) -> bool: ...


class IntakeOwners(Protocol):
    def principal_for(self, key: IntakeKey) -> Principal | None:
        """On whose behalf a website lead is captured: the key's creator while they still
        manage the department, else an active head of that department, else a director.
        None when the company has nobody who could receive it."""
        ...


class LeadIntake(Protocol):
    def capture(self, principal: Principal, key: IntakeKey, submission: WebsiteSubmission) -> UUID:
        """Create the lead through the leads use case (routing included), attach the
        comment/page/UTM note and audit the receipt — in one tenant transaction."""
        ...


class DuplicateGuard(Protocol):
    def claim(self, key_id: UUID, fingerprint: str) -> bool:
        """True the first time this contact is sent through the key within the window."""
        ...

    def release(self, key_id: UUID, fingerprint: str) -> None: ...
