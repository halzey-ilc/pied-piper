from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Company:
    id: UUID
    slug: str
    name: str
    country: str
    currency: str
    timezone: str


@dataclass(frozen=True, slots=True)
class DepartmentInfo:
    id: UUID
    name: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class StaffMember:
    membership_id: UUID
    user_id: UUID
    login: str
    full_name: str
    email: str | None
    phone: str
    role: str
    department: DepartmentInfo | None
    is_active: bool
    must_change_password: bool
    last_login: datetime | None
    created_by: UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NewAccount:
    login: str
    first_name: str
    last_name: str
    email: str | None
    phone: str
    role: str
    department_id: UUID | None
    created_by: UUID


@dataclass(frozen=True, slots=True)
class StaffRequestInfo:
    """A hire or dismissal request of a head of sales, awaiting the director."""

    id: UUID
    kind: str  # hire | dismiss
    status: str  # pending | approved | rejected | cancelled
    department: DepartmentInfo
    requested_by: UUID
    requested_by_name: str
    created_at: datetime
    # hire
    first_name: str
    last_name: str
    login: str
    email: str | None
    phone: str
    role: str
    # dismiss
    target_membership_id: UUID | None
    target_name: str | None
    reason: str
    # decision
    decided_by_name: str | None
    decided_at: datetime | None
    decision_note: str
    result_membership_id: UUID | None


@dataclass(frozen=True, slots=True)
class NewStaffRequest:
    kind: str
    department_id: UUID
    requested_by: UUID
    first_name: str = ""
    last_name: str = ""
    login: str = ""
    email: str | None = None
    phone: str = ""
    role: str = ""
    target_membership_id: UUID | None = None
    reason: str = ""


class OrganizationStore(Protocol):
    """Control-plane data of one company (organizations and identity tables)."""

    def company(self, company_id: UUID) -> Company: ...

    def save_company(self, company: Company) -> None: ...

    def departments(self, company_id: UUID) -> list[DepartmentInfo]: ...

    def department(self, company_id: UUID, department_id: UUID) -> DepartmentInfo | None: ...

    def add_department(self, company_id: UUID, name: str) -> DepartmentInfo: ...

    def save_department(self, company_id: UUID, department: DepartmentInfo) -> None: ...

    def department_name_taken(
        self, company_id: UUID, name: str, exclude: UUID | None = None
    ) -> bool: ...

    def staff(self, company_id: UUID, department_id: UUID | None = None) -> list[StaffMember]: ...

    def member(self, company_id: UUID, membership_id: UUID) -> StaffMember | None: ...

    def login_taken(self, login: str) -> bool: ...

    def email_taken(self, email: str) -> bool: ...

    def create_account(self, company_id: UUID, account: NewAccount, password: str) -> StaffMember:
        """Create the user (must change password) and its membership atomically."""
        ...

    def update_member(
        self,
        company_id: UUID,
        membership_id: UUID,
        *,
        role: str | None = None,
        department_id: UUID | None = None,
        clear_department: bool = False,
    ) -> StaffMember: ...

    def deactivate(self, company_id: UUID, membership_id: UUID) -> StaffMember:
        """Block sign-in (user and membership); history and records stay."""
        ...

    def set_temporary_password(self, company_id: UUID, membership_id: UUID, password: str) -> None:
        """Store a hash only and require a change at next sign-in."""
        ...

    def add_request(self, company_id: UUID, request: NewStaffRequest) -> StaffRequestInfo: ...

    def staff_request(self, company_id: UUID, request_id: UUID) -> StaffRequestInfo | None: ...

    def staff_requests(
        self, company_id: UUID, department_id: UUID | None, status: str | None
    ) -> list[StaffRequestInfo]: ...

    def pending_dismissal_exists(self, membership_id: UUID) -> bool: ...

    def decide_request(
        self,
        company_id: UUID,
        request_id: UUID,
        *,
        status: str,
        decided_by: UUID | None,
        note: str = "",
        result_membership_id: UUID | None = None,
    ) -> StaffRequestInfo:
        """Move a pending request to its final status; ConflictError if no longer pending."""
        ...


class WorkHandover(Protocol):
    """Hands a deactivated member's open tasks to another member (company database)."""

    def hand_over(self, from_user: UUID, to_user: UUID, actor: UUID) -> int: ...


class PasswordGenerator(Protocol):
    def __call__(self) -> str: ...
