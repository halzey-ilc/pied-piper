"""OrganizationStore on the control plane (``default`` database)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from modules.identity.infrastructure.sessions import revoke_credentials
from modules.organizations.application.ports import (
    Company,
    DepartmentHeadTakenError,
    DepartmentInfo,
    NewAccount,
    NewStaffRequest,
    StaffMember,
    StaffRequestInfo,
)
from modules.organizations.infrastructure.models import (
    HEAD_OF_SALES_ROLES,
    Department,
    Membership,
    Organization,
    StaffRequest,
)
from shared.domain.errors import ConflictError, NotFoundError

DB = "default"


def _department(row: Department | None) -> DepartmentInfo | None:
    if row is None:
        return None
    return DepartmentInfo(id=row.id, name=row.name, is_active=row.is_active)


def _member(row: Membership) -> StaffMember:
    user: Any = row.user
    return StaffMember(
        membership_id=row.id,
        user_id=user.id,
        login=user.username,
        full_name=" ".join(p for p in (user.first_name, user.last_name) if p) or user.username,
        email=user.email,
        phone=user.phone,
        role=row.role,
        department=_department(row.department),
        is_active=row.is_active and user.is_active,
        must_change_password=user.must_change_password,
        last_login=user.last_login,
        created_by=row.created_by,
        created_at=row.created_at,
    )


@contextmanager
def _head_guard() -> Iterator[None]:
    """Turn the one-head-per-department constraint into the business error (a race lost)."""
    try:
        yield
    except IntegrityError as exc:
        if "membership_one_active_head_per_department" in str(exc):
            raise DepartmentHeadTakenError() from None
        raise


def _name(user: Any) -> str:
    return " ".join(p for p in (user.first_name, user.last_name) if p) or user.username


def _request(row: StaffRequest, names: dict[Any, str]) -> StaffRequestInfo:
    department = DepartmentInfo(
        id=row.department.id, name=row.department.name, is_active=row.department.is_active
    )
    target = row.target
    return StaffRequestInfo(
        id=row.id,
        kind=row.kind,
        status=row.status,
        department=department,
        requested_by=row.requested_by,
        requested_by_name=names.get(row.requested_by, "—"),
        created_at=row.created_at,
        first_name=row.first_name,
        last_name=row.last_name,
        login=row.login,
        email=row.email or None,
        phone=row.phone,
        role=row.role,
        target_membership_id=row.target_id,
        target_name=_name(target.user) if target is not None else None,
        reason=row.reason,
        decided_by_name=names.get(row.decided_by) if row.decided_by else None,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
        result_membership_id=row.result_id,
    )


def _requests(rows: list[StaffRequest]) -> list[StaffRequestInfo]:
    ids = {r.requested_by for r in rows} | {r.decided_by for r in rows if r.decided_by}
    names = {u.id: _name(u) for u in get_user_model().objects.using(DB).filter(id__in=ids)}
    return [_request(r, names) for r in rows]


class DjangoStaffRequestMixin:
    """Hire and dismissal requests (part of DjangoOrganizationStore)."""

    def _request_rows(self, company_id: UUID) -> Any:
        return (
            StaffRequest.objects.using(DB)
            .filter(organization_id=company_id)
            .select_related("department", "target__user")
        )

    def add_request(self, company_id: UUID, request: NewStaffRequest) -> StaffRequestInfo:
        row = StaffRequest.objects.using(DB).create(
            organization_id=company_id,
            kind=request.kind,
            department_id=request.department_id,
            requested_by=request.requested_by,
            first_name=request.first_name,
            last_name=request.last_name,
            login=request.login,
            email=request.email or "",
            phone=request.phone,
            role=request.role,
            target_id=request.target_membership_id,
            reason=request.reason,
        )
        return self._one(company_id, row.id)

    def staff_request(self, company_id: UUID, request_id: UUID) -> StaffRequestInfo | None:
        row = self._request_rows(company_id).filter(id=request_id).first()
        return None if row is None else _requests([row])[0]

    def staff_requests(
        self, company_id: UUID, department_id: UUID | None, status: str | None
    ) -> list[StaffRequestInfo]:
        qs = self._request_rows(company_id)
        if department_id is not None:
            qs = qs.filter(department_id=department_id)
        if status is not None:
            qs = qs.filter(status=status)
        return _requests(list(qs.order_by("-created_at")[:200]))

    def _one(self, company_id: UUID, request_id: UUID) -> StaffRequestInfo:
        found = self.staff_request(company_id, request_id)
        if found is None:
            raise NotFoundError("Request not found.")
        return found

    def pending_dismissal_exists(self, membership_id: UUID) -> bool:
        return (
            StaffRequest.objects.using(DB)
            .filter(Q(target_id=membership_id), kind="dismiss", status="pending")
            .exists()
        )

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
        updated = (
            StaffRequest.objects.using(DB)
            .filter(organization_id=company_id, id=request_id, status="pending")
            .update(
                status=status,
                decided_by=decided_by,
                decided_at=timezone.now(),
                decision_note=note,
                result_id=result_membership_id,
            )
        )
        if updated != 1:
            raise ConflictError("The request has already been decided.")
        return self._one(company_id, request_id)


class DjangoOrganizationStore(DjangoStaffRequestMixin):
    def _members(self, company_id: UUID) -> Any:
        return (
            Membership.objects.using(DB)
            .filter(organization_id=company_id)
            .select_related("user", "department")
        )

    def company(self, company_id: UUID) -> Company:
        row = Organization.objects.using(DB).filter(id=company_id).first()
        if row is None:
            raise NotFoundError("Company not found.")
        return Company(
            id=row.id,
            slug=row.slug,
            name=row.name,
            country=row.country,
            currency=row.currency,
            timezone=row.timezone,
        )

    def save_company(self, company: Company) -> None:
        Organization.objects.using(DB).filter(id=company.id).update(
            name=company.name,
            country=company.country,
            currency=company.currency,
            timezone=company.timezone,
        )

    def departments(self, company_id: UUID) -> list[DepartmentInfo]:
        rows = Department.objects.using(DB).filter(organization_id=company_id).order_by("name")
        return [DepartmentInfo(id=r.id, name=r.name, is_active=r.is_active) for r in rows]

    def department(self, company_id: UUID, department_id: UUID) -> DepartmentInfo | None:
        return _department(
            Department.objects.using(DB)
            .filter(organization_id=company_id, id=department_id)
            .first()
        )

    def add_department(self, company_id: UUID, name: str) -> DepartmentInfo:
        row = Department.objects.using(DB).create(organization_id=company_id, name=name)
        return DepartmentInfo(id=row.id, name=row.name, is_active=row.is_active)

    def save_department(self, company_id: UUID, department: DepartmentInfo) -> None:
        Department.objects.using(DB).filter(organization_id=company_id, id=department.id).update(
            name=department.name, is_active=department.is_active
        )

    def department_name_taken(
        self, company_id: UUID, name: str, exclude: UUID | None = None
    ) -> bool:
        qs = Department.objects.using(DB).filter(organization_id=company_id, name__iexact=name)
        if exclude is not None:
            qs = qs.exclude(id=exclude)
        return qs.exists()

    def staff(self, company_id: UUID, department_id: UUID | None = None) -> list[StaffMember]:
        qs = self._members(company_id)
        if department_id is not None:
            qs = qs.filter(department_id=department_id)
        return [_member(r) for r in qs.order_by("user__first_name", "user__username")]

    def member(self, company_id: UUID, membership_id: UUID) -> StaffMember | None:
        row = self._members(company_id).filter(id=membership_id).first()
        return None if row is None else _member(row)

    def login_taken(self, login: str) -> bool:
        return get_user_model().objects.using(DB).filter(username__iexact=login).exists()

    def email_taken(self, email: str) -> bool:
        return get_user_model().objects.using(DB).filter(email__iexact=email).exists()

    def create_account(self, company_id: UUID, account: NewAccount, password: str) -> StaffMember:
        with transaction.atomic(using=DB):
            # Not create_user(): it turns a missing e-mail into "" (unique, so the second
            # account without e-mail would clash). NULL may repeat.
            user = get_user_model()(
                username=account.login,
                email=account.email,
                first_name=account.first_name,
                last_name=account.last_name,
                phone=account.phone,
                must_change_password=True,
            )
            user.set_password(password)
            user.save(using=DB)
            with _head_guard():
                row = Membership.objects.using(DB).create(
                    user=user,
                    organization_id=company_id,
                    role=account.role,
                    department_id=account.department_id,
                    created_by=account.created_by,
                )
        return _member(self._members(company_id).get(id=row.id))

    def update_member(
        self,
        company_id: UUID,
        membership_id: UUID,
        *,
        role: str | None = None,
        department_id: UUID | None = None,
        clear_department: bool = False,
    ) -> StaffMember:
        changes: dict[str, Any] = {}
        if role is not None:
            changes["role"] = role
        if department_id is not None:
            changes["department_id"] = department_id
        elif clear_department:
            changes["department_id"] = None
        if changes:
            with transaction.atomic(using=DB), _head_guard():
                Membership.objects.using(DB).filter(
                    organization_id=company_id, id=membership_id
                ).update(**changes)
        return _member(self._members(company_id).get(id=membership_id))

    def active_head(
        self, company_id: UUID, department_id: UUID, exclude_membership: UUID | None = None
    ) -> UUID | None:
        rows = Membership.objects.using(DB).filter(
            organization_id=company_id,
            department_id=department_id,
            is_active=True,
            role__in=HEAD_OF_SALES_ROLES,
        )
        if exclude_membership is not None:
            rows = rows.exclude(id=exclude_membership)
        return rows.values_list("id", flat=True).first()

    def deactivate(self, company_id: UUID, membership_id: UUID) -> StaffMember:
        with transaction.atomic(using=DB):
            row = (
                Membership.objects.using(DB)
                .select_for_update()
                .get(organization_id=company_id, id=membership_id)
            )
            Membership.objects.using(DB).filter(id=row.id).update(
                is_active=False, deactivated_at=timezone.now()
            )
            # One company per person: the account itself stops signing in.
            get_user_model().objects.using(DB).filter(id=row.user_id).update(is_active=False)
            revoke_credentials(row.user_id)
        return _member(self._members(company_id).get(id=membership_id))

    def set_temporary_password(self, company_id: UUID, membership_id: UUID, password: str) -> None:
        row = self._members(company_id).get(id=membership_id)
        user: Any = row.user
        user.set_password(password)  # the previous password stops working at once
        user.must_change_password = True
        user.save(using=DB, update_fields=["password", "must_change_password"])
        revoke_credentials(user.id)  # signed-in sessions and reset links end too
