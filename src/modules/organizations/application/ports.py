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


class PasswordGenerator(Protocol):
    def __call__(self) -> str: ...
