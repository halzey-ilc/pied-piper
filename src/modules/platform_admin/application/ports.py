"""Read models and ports of platform administration (control plane)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from modules.platform_admin.domain.support import ErrorStatus, TicketKind, TicketStatus
from modules.subscriptions.domain.plan import Feature, PlanCode
from modules.subscriptions.domain.subscription import AccessState, SubscriptionStatus


@dataclass(frozen=True, slots=True)
class PlatformActor:
    """Who calls a platform operation; only a platform administrator may."""

    user_id: UUID
    is_platform_admin: bool


@dataclass(frozen=True, slots=True)
class SubscriptionInfo:
    plan_code: PlanCode
    status: SubscriptionStatus
    access_state: AccessState
    starts_on: date
    ends_on: date | None  # last day of access, inclusive
    features: frozenset[Feature] | None  # None = not restricted (pre-catalog subscription)


@dataclass(frozen=True, slots=True)
class StaffCounts:
    """The company's current structure: active accounts only, each person counted once."""

    employees: int  # every active member, the director included
    leaders: int  # director / owner / company admin
    departments: int  # active sales departments
    heads_of_sales: int
    managers: int  # sales managers (sales_rep)


@dataclass(frozen=True, slots=True)
class CompanySummary:
    id: UUID
    slug: str
    name: str
    is_active: bool
    created_at: datetime
    tenant_status: str | None  # provisioning state of the company's database
    users: int  # active accounts
    subscription: SubscriptionInfo | None
    staff: StaffCounts | None = None


@dataclass(frozen=True, slots=True)
class DirectorInfo:
    user_id: UUID
    login: str
    full_name: str
    is_active: bool
    must_change_password: bool
    last_login: datetime | None


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    occurred_at: datetime
    action: str
    actor: str | None
    changed_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompanyDetail:
    summary: CompanySummary
    country: str
    currency: str
    timezone: str
    departments: int
    directors: list[DirectorInfo]
    history: list[HistoryEntry]


@dataclass(frozen=True, slots=True)
class CompanyFilter:
    search: str | None = None
    is_active: bool | None = None
    plan_code: PlanCode | None = None
    access: AccessState | None = None  # None-subscription companies match no access filter


@dataclass(frozen=True, slots=True)
class PlatformCounts:
    companies: int
    active_companies: int
    suspended_companies: int
    new_companies: int  # connected since the period start
    users: int  # active accounts in active companies
    access: dict[AccessState, int]  # companies by subscription access state
    without_subscription: int
    # Plan codes of subscriptions that currently give access (active, expiring, past due).
    paying_plans: dict[PlanCode, int] = field(default_factory=dict)
    errors_new: int = 0
    errors_in_period: int = 0
    tickets_open: int = 0
    tickets_in_period: int = 0


@dataclass(frozen=True, slots=True)
class NewCompanyRecord:
    slug: str
    name: str
    country: str
    currency: str
    timezone: str


@dataclass(frozen=True, slots=True)
class NewDirector:
    first_name: str
    last_name: str
    login: str | None
    email: str | None
    phone: str


@dataclass(frozen=True, slots=True)
class ErrorRecord:
    id: UUID
    occurred_at: datetime
    component: str
    view: str
    method: str
    route: str
    exception_type: str
    request_id: str
    tenant_slug: str
    status: ErrorStatus
    handled_at: datetime | None


@dataclass(frozen=True, slots=True)
class Ticket:
    id: UUID
    company_id: UUID
    company_name: str
    author_id: UUID
    author_name: str
    kind: TicketKind
    subject: str
    body: str
    status: TicketStatus
    internal_note: str
    created_at: datetime
    updated_at: datetime


class PlatformStore(Protocol):
    def list_companies(
        self, filters: CompanyFilter, now: datetime, *, limit: int, offset: int
    ) -> tuple[list[CompanySummary], int]: ...

    def company(self, company_id: UUID, now: datetime) -> CompanyDetail | None: ...

    def counts(self, now: datetime, since: datetime) -> PlatformCounts: ...

    def slug_taken(self, slug: str) -> bool: ...

    def login_taken(self, login: str) -> bool: ...

    def email_taken(self, email: str) -> bool: ...

    def add_company(self, company: NewCompanyRecord) -> UUID: ...

    def add_director(
        self,
        company_id: UUID,
        director: NewDirector,
        login: str,
        password: str,
        created_by: UUID,
    ) -> None: ...

    def update_company(
        self, company_id: UUID, *, name: str | None, is_active: bool | None
    ) -> None: ...

    def director_login(self, company_id: UUID, user_id: UUID) -> str | None:
        """Login of an ACTIVE director of this company; None for anyone else."""
        ...

    def set_temporary_password(self, user_id: UUID, password: str) -> None:
        """Store the hash, require a change at next sign-in, revoke sessions and reset links."""
        ...


class TenantProvisioner(Protocol):
    """Creates, migrates and activates the company's own database (idempotent)."""

    def provision(self, company_id: UUID, slug: str, requested_by: UUID) -> None: ...


class ErrorLog(Protocol):
    def list(
        self, status: ErrorStatus | None, *, limit: int, offset: int
    ) -> tuple[list[ErrorRecord], int]: ...

    def set_status(
        self, error_id: UUID, status: ErrorStatus, actor: UUID
    ) -> ErrorRecord | None: ...


class TicketStore(Protocol):
    def add(
        self, company_id: UUID, author_id: UUID, kind: TicketKind, subject: str, body: str
    ) -> Ticket: ...

    def list(
        self,
        *,
        company_id: UUID | None,
        author_id: UUID | None,
        status: TicketStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Ticket], int]: ...

    def get(self, ticket_id: UUID) -> Ticket | None: ...

    def update(
        self, ticket_id: UUID, *, status: TicketStatus | None, internal_note: str | None
    ) -> Ticket: ...
