"""Platform administration use cases.

Only a platform administrator (``PlatformActor.is_platform_admin``) may call
``PlatformAdminService``; a company role — director included — never grants it.
Company members reach the platform only through ``SupportService`` (their own
company's requests).

A new company is registered in one control-plane transaction: organization,
director account, subscription and the tenant database registration. The
database itself is created and migrated inside that transaction, so a failure
rolls every record back; a database left behind by a failed migration is reused
by the next attempt with the same short name (provisioning is idempotent).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import available_timezones

from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.platform_admin.application.ports import (
    CompanyDetail,
    CompanyFilter,
    CompanySummary,
    ErrorLog,
    ErrorRecord,
    NewCompanyRecord,
    NewDirector,
    PlatformActor,
    PlatformCounts,
    PlatformStore,
    TenantProvisioner,
    Ticket,
    TicketStore,
)
from modules.platform_admin.domain.support import ErrorStatus, TicketKind, TicketStatus
from modules.subscriptions.application.ports import (
    PaymentFilter,
    SubscriptionPaymentRepository,
    SubscriptionRepository,
)
from modules.subscriptions.domain.payment import SubscriptionPayment
from modules.subscriptions.domain.plan import Feature, PlanCode, all_plans, get_plan
from modules.subscriptions.domain.subscription import (
    AccessState,
    Subscription,
    SubscriptionStatus,
)
from platform_core.application.ports import EventOutbox
from platform_core.domain.tenancy import validate_tenant_slug
from shared.application.ports import UnitOfWork
from shared.domain.errors import (
    AccessDeniedError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)

_COUNTRY = re.compile(r"^[A-Z]{2}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_LOGIN = re.compile(r"^[A-Za-z0-9._@+-]{3,150}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True, slots=True)
class Terms:
    """Plan, term and modules as the platform administrator sets them."""

    plan_code: PlanCode
    starts_on: date
    ends_on: date | None  # last day of access, inclusive; None = open-ended
    features: frozenset[Feature] | None = None  # None = the plan's default modules
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class NewCompany:
    name: str
    slug: str
    country: str
    currency: str
    timezone: str
    terms: Terms
    director: NewDirector


@dataclass(frozen=True, slots=True)
class CreatedCompany:
    company: CompanyDetail
    director_login: str
    temporary_password: str  # returned once, never stored in clear


@dataclass(frozen=True, slots=True)
class Overview:
    counts: PlatformCounts
    period_days: int
    # Sum of the monthly list prices of plans that currently give access, per currency.
    # An assigned price, NOT received money.
    assigned_monthly: dict[str, Decimal]
    # CONFIRMED subscription payments paid within the period, per currency.
    received: dict[str, Decimal]
    # The same assigned list prices split by plan, for every plan in the catalog.
    assigned_by_plan: tuple[PlanRevenue, ...] = ()
    plan: PlanCode | None = None  # the plan the overview is narrowed to; None = all


@dataclass(frozen=True, slots=True)
class PlanRevenue:
    plan_code: PlanCode
    companies: int  # subscriptions on this plan that give access now
    currency: str
    monthly: Decimal


@dataclass(frozen=True, slots=True)
class RecordPayment:
    amount: Decimal
    currency: str
    paid_on: date
    reference: str
    period_start: date | None = None
    period_end: date | None = None
    note: str = ""
    confirm: bool = False  # record and confirm in one step (money already seen)


def period_bounds(terms: Terms) -> tuple[datetime, datetime | None]:
    """Dates -> instants: access from the first day 00:00 UTC through the last day."""
    start = datetime.combine(terms.starts_on, time.min, tzinfo=UTC)
    ends_on = terms.ends_on
    trial_days = get_plan(terms.plan_code).trial_days
    if ends_on is None and trial_days is not None:
        ends_on = terms.starts_on + timedelta(days=trial_days - 1)
    end = (
        datetime.combine(ends_on + timedelta(days=1), time.min, tzinfo=UTC)
        if ends_on is not None
        else None
    )
    return start, end


class PlatformAdminService:
    def __init__(
        self,
        store: PlatformStore,
        subscriptions: SubscriptionRepository,
        provisioner: TenantProvisioner,
        errors: ErrorLog,
        tickets: TicketStore,
        payments: SubscriptionPaymentRepository,
        uow: UnitOfWork,
        outbox: EventOutbox,
        audit: AuditLog,
        passwords: Callable[[], str],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._subscriptions = subscriptions
        self._provisioner = provisioner
        self._errors = errors
        self._tickets = tickets
        self._payments = payments
        self._uow = uow
        self._outbox = outbox
        self._audit = audit
        self._passwords = passwords
        self._now = clock

    # --- overview -------------------------------------------------------------

    def overview(
        self, actor: PlatformActor, period_days: int, plan: PlanCode | None = None
    ) -> Overview:
        _require(actor)
        if period_days not in (7, 30, 90, 365):
            raise DomainValidationError("period_days must be 7, 30, 90 or 365.")
        now = self._now()
        counts = self._store.counts(now, now - timedelta(days=period_days), plan)
        assigned: dict[str, Decimal] = {}
        by_plan: list[PlanRevenue] = []
        for p in all_plans():
            n = counts.paying_plans.get(p.code, 0)
            by_plan.append(PlanRevenue(p.code, n, p.currency, p.monthly_price * n))
            if n and p.monthly_price:
                assigned[p.currency] = assigned.get(p.currency, Decimal(0)) + (p.monthly_price * n)
        received = self._payments.confirmed_totals(
            (now - timedelta(days=period_days)).date(), now.date()
        )
        return Overview(
            counts=counts,
            period_days=period_days,
            assigned_monthly=assigned,
            received=received,
            assigned_by_plan=tuple(by_plan),
            plan=plan,
        )

    # --- companies ------------------------------------------------------------

    def companies(
        self, actor: PlatformActor, filters: CompanyFilter, *, limit: int, offset: int
    ) -> tuple[list[CompanySummary], int]:
        _require(actor)
        return self._store.list_companies(filters, self._now(), limit=limit, offset=offset)

    def company(self, actor: PlatformActor, company_id: UUID) -> CompanyDetail:
        _require(actor)
        detail = self._store.company(company_id, self._now())
        if detail is None:
            raise NotFoundError("Company not found.")
        return detail

    def create_company(self, actor: PlatformActor, command: NewCompany) -> CreatedCompany:
        _require(actor)
        record = NewCompanyRecord(
            slug=validate_tenant_slug(command.slug.strip().lower()),
            name=_clean(command.name, "name", 200),
            country=_match(_COUNTRY, command.country, "country must be ISO 3166-1, e.g. KG."),
            currency=_match(_CURRENCY, command.currency, "currency must be ISO 4217, e.g. KGS."),
            timezone=_timezone(command.timezone),
        )
        director = _director(command.director)
        features = _features(command.terms)
        started_at, period_end = period_bounds(command.terms)
        password = self._passwords()
        with self._uow:
            if self._store.slug_taken(record.slug):
                raise ConflictError("A company with this short name already exists.")
            login = director.login or _login_from(director.first_name, director.last_name)
            if self._store.login_taken(login):
                if director.login:
                    raise ConflictError("This login is already taken.")
                login = _unique_login(login, self._store.login_taken)
            if director.email and self._store.email_taken(director.email):
                raise ConflictError("This e-mail is already used by another account.")
            company_id = self._store.add_company(record)
            self._store.add_director(company_id, director, login, password, actor.user_id)
            subscription, event = Subscription.assigned(
                organization_id=company_id,
                plan_code=command.terms.plan_code,
                started_at=started_at,
                period_end=period_end,
                features=features,
            )
            self._subscriptions.add(subscription)
            self._outbox.add(
                event, aggregate_type="subscription", aggregate_id=str(subscription.id)
            )
            self._record(actor, "platform.company_created", company_id, ("name", "slug"))
            self._record(
                actor, "platform.subscription_assigned", company_id, ("plan", "term", "modules")
            )
            # Last: creates the company's database; any failure rolls back the records above.
            self._provisioner.provision(company_id, record.slug, actor.user_id)
        return CreatedCompany(
            company=self.company(actor, company_id),
            director_login=login,
            temporary_password=password,
        )

    def update_company(
        self,
        actor: PlatformActor,
        company_id: UUID,
        *,
        name: str | None = None,
        is_active: bool | None = None,
    ) -> CompanyDetail:
        _require(actor)
        clean = _clean(name, "name", 200) if name is not None else None
        with self._uow:
            self.company(actor, company_id)
            self._store.update_company(company_id, name=clean, is_active=is_active)
            changed = tuple(
                f for f, v in (("name", clean), ("is_active", is_active)) if v is not None
            )
            action = (
                "platform.company_suspended"
                if is_active is False
                else "platform.company_resumed"
                if is_active is True
                else "platform.company_updated"
            )
            self._record(actor, action, company_id, changed)
        return self.company(actor, company_id)

    def issue_director_credentials(
        self, actor: PlatformActor, company_id: UUID, user_id: UUID
    ) -> tuple[str, str]:
        """Restore a director's access: a NEW temporary password (shown once), never the old one.

        The previous password, signed-in sessions and unused reset links stop working; the
        director must change the password at the next sign-in. Covers both a forgotten
        password and lost initial credentials. Returns (login, temporary password).
        """
        _require(actor)
        password = self._passwords()
        with self._uow:
            self.company(actor, company_id)
            login = self._store.director_login(company_id, user_id)
            if login is None:
                raise NotFoundError("No active director with this id in the company.")
            self._store.set_temporary_password(user_id, password)
            self._record(actor, "platform.director_credentials_issued", company_id, ("password",))
        return login, password

    def set_subscription(
        self, actor: PlatformActor, company_id: UUID, terms: Terms
    ) -> CompanyDetail:
        """Assign plan, term, modules and status. Not a payment record."""
        _require(actor)
        features = _features(terms)
        started_at, period_end = period_bounds(terms)
        with self._uow:
            self.company(actor, company_id)
            subscription = self._subscriptions.get_by_org(company_id)
            if subscription is None:
                subscription, event = Subscription.assigned(
                    organization_id=company_id,
                    plan_code=terms.plan_code,
                    started_at=started_at,
                    period_end=period_end,
                    features=features,
                )
                if terms.status is not SubscriptionStatus.ACTIVE:
                    subscription.assign(
                        plan_code=terms.plan_code,
                        started_at=started_at,
                        period_end=period_end,
                        features=features,
                        status=terms.status,
                    )
                self._subscriptions.add(subscription)
            else:
                version = subscription.version
                event = subscription.assign(
                    plan_code=terms.plan_code,
                    started_at=started_at,
                    period_end=period_end,
                    features=features,
                    status=terms.status,
                )
                self._subscriptions.save(subscription, expected_version=version)
            self._outbox.add(
                event, aggregate_type="subscription", aggregate_id=str(subscription.id)
            )
            self._record(
                actor,
                "platform.subscription_assigned",
                company_id,
                ("plan", "term", "modules", "status"),
            )
        return self.company(actor, company_id)

    # --- plan limits ----------------------------------------------------------------

    def subscription_of(self, actor: PlatformActor, company_id: UUID) -> Subscription | None:
        """The company's subscription (None before one is assigned); 404 for no company."""
        self.company(actor, company_id)
        return self._subscriptions.get_by_org(company_id)

    def set_limits(
        self, actor: PlatformActor, company_id: UUID, overrides: dict[str, int] | None
    ) -> Subscription:
        """Per-company limits on top of the plan; empty or None returns to the plan's own."""
        _require(actor)
        with self._uow:
            self.company(actor, company_id)
            subscription = self._subscriptions.get_by_org(company_id)
            if subscription is None:
                raise ConflictError("Assign a subscription before changing its limits.")
            version = subscription.version
            subscription.set_limits(overrides)
            self._subscriptions.save(subscription, expected_version=version)
            self._record(
                actor,
                "platform.limits_changed",
                company_id,
                tuple(sorted((overrides or {}).keys())) or ("limits",),
            )
        return subscription

    # --- subscription payments --------------------------------------------------

    def record_payment(
        self, actor: PlatformActor, company_id: UUID, command: RecordPayment
    ) -> SubscriptionPayment:
        """Record money the platform received for a company's subscription (manual entry).

        The same external reference cannot be recorded twice (409 duplicate_payment).
        """
        _require(actor)
        with self._uow:
            self.company(actor, company_id)
            subscription = self._subscriptions.get_by_org(company_id)
            payment = SubscriptionPayment.record(
                organization_id=company_id,
                subscription_id=subscription.id if subscription else None,
                amount=command.amount,
                currency=command.currency,
                paid_on=command.paid_on,
                reference=command.reference,
                recorded_by=actor.user_id,
                period_start=command.period_start,
                period_end=command.period_end,
                note=command.note,
            )
            if command.paid_on > self._now().date():
                raise DomainValidationError("paid_on must not be in the future.")
            self._payments.add(payment)
            self._record(actor, "platform.payment_recorded", company_id, ("payment",))
            if command.confirm:
                payment.confirm(actor.user_id)
                self._payments.save_decision(payment)
                self._record(actor, "platform.payment_confirmed", company_id, ("payment",))
        return payment

    def confirm_payment(self, actor: PlatformActor, payment_id: UUID) -> SubscriptionPayment:
        return self._decide_payment(actor, payment_id, confirm=True, note="")

    def cancel_payment(
        self, actor: PlatformActor, payment_id: UUID, note: str = ""
    ) -> SubscriptionPayment:
        return self._decide_payment(actor, payment_id, confirm=False, note=note)

    def payments(
        self, actor: PlatformActor, filters: PaymentFilter, *, limit: int, offset: int
    ) -> tuple[list[SubscriptionPayment], int]:
        _require(actor)
        return self._payments.list(filters, limit=limit, offset=offset)

    def _decide_payment(
        self, actor: PlatformActor, payment_id: UUID, *, confirm: bool, note: str
    ) -> SubscriptionPayment:
        _require(actor)
        with self._uow:
            payment = self._payments.get(payment_id)
            if payment is None:
                raise NotFoundError("Payment not found.")
            if confirm:
                payment.confirm(actor.user_id)
            else:
                payment.cancel(actor.user_id, note)
            self._payments.save_decision(payment)
            action = "platform.payment_confirmed" if confirm else "platform.payment_cancelled"
            self._record(actor, action, payment.organization_id, ("payment",))
        return payment

    # --- errors ---------------------------------------------------------------

    def errors(
        self, actor: PlatformActor, status: ErrorStatus | None, *, limit: int, offset: int
    ) -> tuple[list[ErrorRecord], int]:
        _require(actor)
        return self._errors.list(status, limit=limit, offset=offset)

    def set_error_status(
        self, actor: PlatformActor, error_id: UUID, status: ErrorStatus
    ) -> ErrorRecord:
        _require(actor)
        record = self._errors.set_status(error_id, status, actor.user_id)
        if record is None:
            raise NotFoundError("Error record not found.")
        return record

    # --- support requests -------------------------------------------------------

    def tickets(
        self, actor: PlatformActor, status: TicketStatus | None, *, limit: int, offset: int
    ) -> tuple[list[Ticket], int]:
        _require(actor)
        return self._tickets.list(
            company_id=None, author_id=None, status=status, limit=limit, offset=offset
        )

    def ticket(self, actor: PlatformActor, ticket_id: UUID) -> Ticket:
        _require(actor)
        ticket = self._tickets.get(ticket_id)
        if ticket is None:
            raise NotFoundError("Request not found.")
        return ticket

    def update_ticket(
        self,
        actor: PlatformActor,
        ticket_id: UUID,
        *,
        status: TicketStatus | None,
        internal_note: str | None,
    ) -> Ticket:
        _require(actor)
        self.ticket(actor, ticket_id)
        note = internal_note.strip()[:5000] if internal_note is not None else None
        return self._tickets.update(ticket_id, status=status, internal_note=note)

    # --- helpers ----------------------------------------------------------------

    def _record(
        self, actor: PlatformActor, action: str, company_id: UUID, fields: tuple[str, ...]
    ) -> None:
        self._audit.record(
            AuditEntry(
                actor_user_id=actor.user_id,
                action=action,
                resource_type="organization",
                resource_id=str(company_id),
                changed_fields=fields,
            )
        )


class SupportService:
    """A company member's side of support requests: send and follow their own."""

    def __init__(self, tickets: TicketStore) -> None:
        self._tickets = tickets

    def send(self, company_id: UUID, author_id: UUID, kind: str, subject: str, body: str) -> Ticket:
        try:
            ticket_kind = TicketKind(kind)
        except ValueError as exc:
            raise DomainValidationError("Unknown request kind.") from exc
        return self._tickets.add(
            company_id,
            author_id,
            ticket_kind,
            _clean(subject, "subject", 200),
            _text(body, "body", 5000),
        )

    def mine(
        self, company_id: UUID, author_id: UUID, *, limit: int, offset: int
    ) -> tuple[list[Ticket], int]:
        """Only the author's own requests: colleagues' messages may be confidential."""
        return self._tickets.list(
            company_id=company_id, author_id=author_id, status=None, limit=limit, offset=offset
        )


def _require(actor: PlatformActor) -> None:
    if not actor.is_platform_admin:
        raise AccessDeniedError("Only the platform administrator can do this.")


def _features(terms: Terms) -> frozenset[Feature]:
    get_plan(terms.plan_code)
    return terms.features if terms.features is not None else get_plan(terms.plan_code).features


def _director(director: NewDirector) -> NewDirector:
    login = (director.login or "").strip() or None
    if login is not None and not _LOGIN.match(login):
        raise DomainValidationError("login may contain letters, digits and . _ @ + - (3-150).")
    email = (director.email or "").strip().lower() or None
    if email is not None and not _EMAIL.match(email):
        raise DomainValidationError("email is not a valid e-mail address.")
    return NewDirector(
        first_name=_clean(director.first_name, "first_name", 150),
        last_name=" ".join(director.last_name.split())[:150],
        login=login,
        email=email,
        phone=director.phone.strip()[:16],
    )


def _timezone(value: str) -> str:
    if value not in available_timezones():
        raise DomainValidationError("timezone must be an IANA name, e.g. Asia/Bishkek.")
    return value


def _match(pattern: re.Pattern[str], value: str, message: str) -> str:
    if not pattern.match(value):
        raise DomainValidationError(message)
    return value


def _clean(value: str, field: str, limit: int) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise DomainValidationError(f"{field} must not be empty.")
    if len(cleaned) > limit:
        raise DomainValidationError(f"{field} must be at most {limit} characters.")
    return cleaned


def _text(value: str, field: str, limit: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise DomainValidationError(f"{field} must not be empty.")
    if len(cleaned) > limit:
        raise DomainValidationError(f"{field} must be at most {limit} characters.")
    return cleaned


def _login_from(first: str, last: str) -> str:
    base = ".".join(p for p in (first, last) if p).lower()
    ascii_only = re.sub(r"[^a-z0-9.]+", "", base)
    return ascii_only if len(ascii_only) >= 3 else "director"


def _unique_login(base: str, taken: Callable[[str], bool]) -> str:
    for n in range(2, 1000):
        candidate = f"{base}{n}"
        if not taken(candidate):
            return candidate
    raise ConflictError("Could not generate a free login; please choose one.")


__all__ = ["AccessState", "PlatformAdminService", "SupportService", "Terms"]
