"""OrganizationStore on the control plane (``default`` database)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from modules.organizations.application.ports import (
    Company,
    DepartmentInfo,
    NewAccount,
    StaffMember,
)
from modules.organizations.infrastructure.models import Department, Membership, Organization
from shared.domain.errors import NotFoundError

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


class DjangoOrganizationStore:
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
            Membership.objects.using(DB).filter(
                organization_id=company_id, id=membership_id
            ).update(**changes)
        return _member(self._members(company_id).get(id=membership_id))

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
        return _member(self._members(company_id).get(id=membership_id))

    def set_temporary_password(self, company_id: UUID, membership_id: UUID, password: str) -> None:
        row = self._members(company_id).get(id=membership_id)
        user: Any = row.user
        user.set_password(password)
        user.must_change_password = True
        user.save(using=DB, update_fields=["password", "must_change_password"])
