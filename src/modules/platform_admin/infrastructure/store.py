"""ORM adapters of platform administration (control plane, the default database)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Count, Q, QuerySet

from modules.access_control.domain.rbac import Role
from modules.audit.infrastructure.models import AuditRecord
from modules.identity.infrastructure.sessions import revoke_credentials
from modules.organizations.infrastructure.models import Department, Membership, Organization
from modules.platform_admin.application.ports import (
    CompanyDetail,
    CompanyFilter,
    CompanySummary,
    DirectorInfo,
    ErrorRecord,
    HistoryEntry,
    NewCompanyRecord,
    NewDirector,
    PlatformCounts,
    SubscriptionInfo,
    Ticket,
)
from modules.platform_admin.domain.support import ErrorStatus, TicketKind, TicketStatus
from modules.platform_admin.infrastructure.models import ErrorEvent, SupportTicket
from modules.sales_pipeline.composition import ensure_starter_pipeline
from modules.subscriptions.domain.plan import PlanCode
from modules.subscriptions.domain.subscription import (
    EXPIRY_WARNING_DAYS,
    AccessState,
    SubscriptionStatus,
)
from modules.subscriptions.infrastructure.models import SubscriptionRecord
from modules.subscriptions.infrastructure.repository import _to_domain
from modules.tenant_registry.application.provisioning import ProvisionTenantCommand
from modules.tenant_registry.composition import build_provision_tenant
from platform_core.infrastructure.tenancy.services import system_tenant_scope

DB = "default"
_DIRECTOR_ROLES = (Role.OWNER.value, Role.DIRECTOR.value, Role.ADMIN.value)
_HISTORY_LIMIT = 50


def _subscription(row: SubscriptionRecord | None, now: datetime) -> SubscriptionInfo | None:
    if row is None:
        return None
    sub = _to_domain(row)
    end = sub.current_period_end
    return SubscriptionInfo(
        plan_code=sub.plan_code,
        status=sub.status,
        access_state=sub.access_state(now),
        starts_on=sub.started_at.astimezone(UTC).date(),
        ends_on=(end - timedelta(days=1)).astimezone(UTC).date() if end else None,
        features=sub.features,
    )


def _access_q(state: AccessState, now: datetime) -> Q:
    soon = now + timedelta(days=EXPIRY_WARNING_DAYS)
    cancelled = Q(subscription__status=SubscriptionStatus.CANCELLED.value)
    running = Q(subscription__current_period_end__isnull=True) | Q(
        subscription__current_period_end__gt=now
    )
    if state is AccessState.CANCELLED:
        return cancelled
    if state is AccessState.EXPIRED:
        return ~cancelled & Q(subscription__current_period_end__lte=now)
    if state is AccessState.PAST_DUE:
        return Q(subscription__status=SubscriptionStatus.PAST_DUE.value) & running
    active = Q(subscription__status=SubscriptionStatus.ACTIVE.value)
    if state is AccessState.EXPIRING:
        return (
            active
            & Q(subscription__current_period_end__gt=now)
            & Q(subscription__current_period_end__lt=soon)
        )
    return active & (
        Q(subscription__current_period_end__isnull=True)
        | Q(subscription__current_period_end__gte=soon)
    )


def _active_users() -> Q:
    return Q(memberships__is_active=True, memberships__user__is_active=True)


class DjangoDirectorCredentialsMixin:
    """Part of DjangoPlatformStore: restoring a director's access."""

    def director_login(self, company_id: UUID, user_id: UUID) -> str | None:
        row = (
            Membership.objects.using(DB)
            .filter(
                organization_id=company_id,
                user_id=user_id,
                role__in=_DIRECTOR_ROLES,
                is_active=True,
                user__is_active=True,
            )
            .select_related("user")
            .first()
        )
        return None if row is None else str(row.user.username)

    def set_temporary_password(self, user_id: UUID, password: str) -> None:
        user = get_user_model().objects.using(DB).get(id=user_id)
        user.set_password(password)  # hash only; the previous password stops working
        user.must_change_password = True
        user.save(using=DB, update_fields=["password", "must_change_password"])
        revoke_credentials(user_id)


class DjangoPlatformStore(DjangoDirectorCredentialsMixin):
    def _companies(self) -> QuerySet[Organization]:
        return (
            Organization.objects.using(DB)
            .select_related("subscription", "tenant_database")
            .annotate(users=Count("memberships", filter=_active_users(), distinct=True))
        )

    def _summary(self, org: Any, now: datetime) -> CompanySummary:
        sub = org.subscription if _has(org, "subscription") else None
        tenant = org.tenant_database if _has(org, "tenant_database") else None
        return CompanySummary(
            id=org.id,
            slug=org.slug,
            name=org.name,
            is_active=org.is_active,
            created_at=org.created_at,
            tenant_status=tenant.status if tenant else None,
            users=org.users,
            subscription=_subscription(sub, now),
        )

    def list_companies(
        self, filters: CompanyFilter, now: datetime, *, limit: int, offset: int
    ) -> tuple[list[CompanySummary], int]:
        qs = self._companies()
        if filters.search:
            qs = qs.filter(Q(name__icontains=filters.search) | Q(slug__icontains=filters.search))
        if filters.is_active is not None:
            qs = qs.filter(is_active=filters.is_active)
        if filters.plan_code is not None:
            qs = qs.filter(subscription__plan_code=filters.plan_code.value)
        if filters.access is not None:
            qs = qs.filter(_access_q(filters.access, now))
        total = qs.count()
        rows = qs.order_by("-created_at", "slug")[offset : offset + limit]
        return [self._summary(org, now) for org in rows], total

    def company(self, company_id: UUID, now: datetime) -> CompanyDetail | None:
        org = self._companies().filter(id=company_id).first()
        if org is None:
            return None
        directors = (
            Membership.objects.using(DB)
            .filter(organization_id=company_id, role__in=_DIRECTOR_ROLES)
            .select_related("user")
            .order_by("created_at")
        )
        records = list(
            AuditRecord.objects.using(DB)
            .filter(resource_type="organization", resource_id=str(company_id))
            .order_by("-occurred_at")[:_HISTORY_LIMIT]
        )
        actors = {
            u.id: _full_name(u)
            for u in get_user_model()
            .objects.using(DB)
            .filter(id__in={r.actor_user_id for r in records if r.actor_user_id})
        }
        return CompanyDetail(
            summary=self._summary(org, now),
            country=org.country,
            currency=org.currency,
            timezone=org.timezone,
            departments=Department.objects.using(DB)
            .filter(organization_id=company_id, is_active=True)
            .count(),
            directors=[
                DirectorInfo(
                    user_id=m.user_id,
                    login=m.user.username,
                    full_name=_full_name(m.user),
                    is_active=m.is_active and m.user.is_active,
                    must_change_password=m.user.must_change_password,
                    last_login=m.user.last_login,
                )
                for m in directors
            ],
            history=[
                HistoryEntry(
                    occurred_at=r.occurred_at,
                    action=r.action,
                    actor=actors.get(r.actor_user_id) if r.actor_user_id else None,
                    changed_fields=tuple(r.changed_fields or ()),
                )
                for r in records
            ],
        )

    def counts(self, now: datetime, since: datetime) -> PlatformCounts:
        orgs = Organization.objects.using(DB)
        companies = orgs.count()
        active = orgs.filter(is_active=True).count()
        access = {
            state: orgs.filter(subscription__isnull=False).filter(_access_q(state, now)).count()
            for state in AccessState
        }
        giving_access = (
            SubscriptionRecord.objects.using(DB)
            .filter(organization__is_active=True)
            .exclude(status=SubscriptionStatus.CANCELLED.value)
            .filter(Q(current_period_end__isnull=True) | Q(current_period_end__gt=now))
            .values("plan_code")
            .annotate(n=Count("id"))
        )
        errors = ErrorEvent.objects.using(DB)
        tickets = SupportTicket.objects.using(DB)
        open_statuses = (TicketStatus.NEW.value, TicketStatus.IN_PROGRESS.value)
        return PlatformCounts(
            companies=companies,
            active_companies=active,
            suspended_companies=companies - active,
            new_companies=orgs.filter(created_at__gte=since).count(),
            users=get_user_model()
            .objects.using(DB)
            .filter(
                is_active=True,
                memberships__is_active=True,
                memberships__organization__is_active=True,
            )
            .distinct()
            .count(),
            access=access,
            without_subscription=orgs.filter(subscription__isnull=True).count(),
            paying_plans={PlanCode(r["plan_code"]): r["n"] for r in giving_access},
            errors_new=errors.filter(status=ErrorStatus.NEW.value).count(),
            errors_in_period=errors.filter(occurred_at__gte=since).count(),
            tickets_open=tickets.filter(status__in=open_statuses).count(),
            tickets_in_period=tickets.filter(created_at__gte=since).count(),
        )

    def slug_taken(self, slug: str) -> bool:
        return Organization.objects.using(DB).filter(slug=slug).exists()

    def login_taken(self, login: str) -> bool:
        return get_user_model().objects.using(DB).filter(username__iexact=login).exists()

    def email_taken(self, email: str) -> bool:
        return get_user_model().objects.using(DB).filter(email__iexact=email).exists()

    def add_company(self, company: NewCompanyRecord) -> UUID:
        org = Organization.objects.using(DB).create(
            slug=company.slug,
            name=company.name,
            country=company.country,
            currency=company.currency,
            timezone=company.timezone,
        )
        return UUID(str(org.id))

    def add_director(
        self,
        company_id: UUID,
        director: NewDirector,
        login: str,
        password: str,
        created_by: UUID,
    ) -> None:
        with transaction.atomic(using=DB):
            user = get_user_model()(
                username=login,
                email=director.email,
                first_name=director.first_name,
                last_name=director.last_name,
                phone=director.phone,
                must_change_password=True,
            )
            user.set_password(password)  # Django's password hasher; never stored in clear
            user.save(using=DB)
            Membership.objects.using(DB).create(
                user=user,
                organization_id=company_id,
                role=Role.DIRECTOR.value,
                created_by=created_by,
            )

    def update_company(self, company_id: UUID, *, name: str | None, is_active: bool | None) -> None:
        changes: dict[str, Any] = {}
        if name is not None:
            changes["name"] = name
        if is_active is not None:
            changes["is_active"] = is_active
        if changes:
            Organization.objects.using(DB).filter(id=company_id).update(**changes)


class DjangoTenantProvisioner:
    def provision(self, company_id: UUID, slug: str, requested_by: UUID) -> None:
        build_provision_tenant().execute(
            ProvisionTenantCommand(organization_id=company_id, slug=slug, requested_by=requested_by)
        )
        # Starter settings in the company's own database: the standard sales pipeline.
        with system_tenant_scope(slug) as tenant:
            ensure_starter_pipeline(tenant.db_alias)


def _error(row: ErrorEvent) -> ErrorRecord:
    return ErrorRecord(
        id=row.id,
        occurred_at=row.occurred_at,
        component=row.component,
        view=row.view,
        method=row.method,
        route=row.route,
        exception_type=row.exception_type,
        request_id=row.request_id,
        tenant_slug=row.tenant_slug,
        status=ErrorStatus(row.status),
        handled_at=row.handled_at,
    )


class DjangoErrorLog:
    def list(
        self, status: ErrorStatus | None, *, limit: int, offset: int
    ) -> tuple[list[ErrorRecord], int]:
        qs = ErrorEvent.objects.using(DB).order_by("-occurred_at")
        if status is not None:
            qs = qs.filter(status=status.value)
        return [_error(r) for r in qs[offset : offset + limit]], qs.count()

    def set_status(self, error_id: UUID, status: ErrorStatus, actor: UUID) -> ErrorRecord | None:
        updated = (
            ErrorEvent.objects.using(DB)
            .filter(id=error_id)
            .update(status=status.value, handled_by=actor, handled_at=datetime.now(UTC))
        )
        if not updated:
            return None
        return _error(ErrorEvent.objects.using(DB).get(id=error_id))


class DjangoErrorRecorder:
    """ErrorRecorder adapter (port in platform_core.application.ports)."""

    def record(
        self,
        *,
        component: str,
        view: str,
        method: str,
        route: str,
        exception_type: str,
        request_id: str | None,
        tenant_slug: str | None,
    ) -> None:
        ErrorEvent.objects.using(DB).create(
            component=component[:100],
            view=view[:100],
            method=method[:10],
            route=route[:200],
            exception_type=exception_type[:100],
            request_id=(request_id or "")[:64],
            tenant_slug=(tenant_slug or "")[:40],
        )


def _ticket(row: SupportTicket) -> Ticket:
    return Ticket(
        id=row.id,
        company_id=row.organization_id,
        company_name=row.organization.name,
        author_id=row.author_id,
        author_name=_full_name(row.author),
        kind=TicketKind(row.kind),
        subject=row.subject,
        body=row.body,
        status=TicketStatus(row.status),
        internal_note=row.internal_note,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class DjangoTicketStore:
    def _qs(self) -> QuerySet[SupportTicket]:
        return SupportTicket.objects.using(DB).select_related("organization", "author")

    def add(
        self, company_id: UUID, author_id: UUID, kind: TicketKind, subject: str, body: str
    ) -> Ticket:
        row = SupportTicket.objects.using(DB).create(
            organization_id=company_id,
            author_id=author_id,
            kind=kind.value,
            subject=subject,
            body=body,
        )
        return _ticket(self._qs().get(id=row.id))

    def list(
        self,
        *,
        company_id: UUID | None,
        author_id: UUID | None,
        status: TicketStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Ticket], int]:
        qs = self._qs().order_by("-created_at")
        if company_id is not None:
            qs = qs.filter(organization_id=company_id)
        if author_id is not None:
            qs = qs.filter(author_id=author_id)
        if status is not None:
            qs = qs.filter(status=status.value)
        return [_ticket(r) for r in qs[offset : offset + limit]], qs.count()

    def get(self, ticket_id: UUID) -> Ticket | None:
        row = self._qs().filter(id=ticket_id).first()
        return None if row is None else _ticket(row)

    def update(
        self, ticket_id: UUID, *, status: TicketStatus | None, internal_note: str | None
    ) -> Ticket:
        row = self._qs().get(id=ticket_id)
        if status is not None:
            row.status = status.value
        if internal_note is not None:
            row.internal_note = internal_note
        row.save(using=DB, update_fields=["status", "internal_note", "updated_at"])
        return _ticket(row)


def _has(obj: Any, relation: str) -> bool:
    try:
        getattr(obj, relation)
    except ObjectDoesNotExist:  # a reverse one-to-one without a row
        return False
    return True


def _full_name(user: Any) -> str:
    return " ".join(p for p in (user.first_name, user.last_name) if p) or user.get_username()
