"""Adapters of the web_forms ports."""

from __future__ import annotations

import hashlib
from datetime import datetime
from uuid import UUID

from django.core.cache import cache
from django.db.models import F, QuerySet

from modules.access_control.domain.rbac import Principal, Role
from modules.organizations.infrastructure.models import Department, Membership
from modules.tenant_registry.infrastructure.models import TenantDatabase
from modules.web_forms.domain.key import IntakeKey
from modules.web_forms.infrastructure.models import WebFormKeyRecord

_DIRECTOR_ROLES = (Role.OWNER.value, Role.DIRECTOR.value, Role.ADMIN.value)
_HEAD_ROLES = (Role.HEAD_OF_SALES.value, Role.MANAGER.value)
DUPLICATE_WINDOW_SECONDS = 10 * 60


def _key(row: WebFormKeyRecord) -> IntakeKey:
    return IntakeKey(
        id=row.id,
        tenant_id=row.tenant_id,
        department_id=row.department_id,
        name=row.name,
        token=row.token,
        created_by=row.created_by,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
        revoked_by=row.revoked_by,
        last_used_at=row.last_used_at,
        leads_count=row.leads_count,
    )


class DjangoKeyStore:
    """Always the control-plane database, whatever tenant context is active."""

    def _rows(self) -> QuerySet[WebFormKeyRecord]:
        return WebFormKeyRecord.objects.using("default")

    def list(self, tenant_id: UUID, department_id: UUID | None) -> list[IntakeKey]:
        rows = self._rows().filter(tenant_id=tenant_id)
        if department_id is not None:
            rows = rows.filter(department_id=department_id)
        return [_key(row) for row in rows.order_by("-created_at")]

    def get(self, tenant_id: UUID, key_id: UUID) -> IntakeKey | None:
        row = self._rows().filter(tenant_id=tenant_id, id=key_id).first()
        return _key(row) if row is not None else None

    def add(self, key: IntakeKey) -> None:
        self._rows().create(
            id=key.id,
            tenant_id=key.tenant_id,
            department_id=key.department_id,
            name=key.name,
            token=key.token,
            created_by=key.created_by,
            created_at=key.created_at,
        )

    def revoke(self, key_id: UUID, *, at: datetime, by: UUID) -> IntakeKey:
        self._rows().filter(id=key_id, revoked_at__isnull=True).update(revoked_at=at, revoked_by=by)
        return _key(self._rows().get(id=key_id))

    def count_active(self, tenant_id: UUID, department_id: UUID) -> int:
        return (
            self._rows()
            .filter(tenant_id=tenant_id, department_id=department_id, revoked_at__isnull=True)
            .count()
        )

    def find_active(self, token: str) -> IntakeKey | None:
        row = self._rows().filter(token=token, revoked_at__isnull=True).first()
        return _key(row) if row is not None else None

    def mark_used(self, key_id: UUID, *, at: datetime) -> None:
        self._rows().filter(id=key_id).update(leads_count=F("leads_count") + 1, last_used_at=at)


class DjangoDepartments:
    def exists(self, tenant_id: UUID, department_id: UUID) -> bool:
        return (
            Department.objects.using("default")
            .filter(organization_id=tenant_id, id=department_id, is_active=True)
            .exists()
        )


def _active_members(tenant_id: UUID) -> QuerySet[Membership]:
    return Membership.objects.using("default").filter(
        organization_id=tenant_id,
        is_active=True,
        organization__is_active=True,
        user__is_active=True,
    )


def _principal(row: Membership, tenant_id: UUID, department_id: UUID) -> Principal:
    teams = {department_id}
    if row.department_id is not None:
        teams.add(row.department_id)
    return Principal(
        user_id=row.user_id,
        tenant_id=tenant_id,
        role=Role(row.role),
        team_ids=frozenset(teams),
        department_id=department_id,
    )


class DjangoIntakeOwners:
    """The principal a website lead is captured as (see the IntakeOwners port)."""

    def principal_for(self, key: IntakeKey) -> Principal | None:
        members = _active_members(key.tenant_id)
        creator = members.filter(user_id=key.created_by).first()
        if creator is not None and (
            creator.role in _DIRECTOR_ROLES
            or (creator.role in _HEAD_ROLES and creator.department_id == key.department_id)
        ):
            return _principal(creator, key.tenant_id, key.department_id)
        fallback = (
            members.filter(role__in=_HEAD_ROLES, department_id=key.department_id)
            .order_by("created_at")
            .first()
        ) or members.filter(role__in=_DIRECTOR_ROLES).order_by("created_at").first()
        return _principal(fallback, key.tenant_id, key.department_id) if fallback else None


class CacheDuplicateGuard:
    """A repeated submission of the same contact through the same key within
    ``DUPLICATE_WINDOW_SECONDS`` is accepted but creates nothing (double clicks, re-sends).
    The contact is hashed: no personal data in cache keys."""

    def _cache_key(self, key_id: UUID, fingerprint: str) -> str:
        digest = hashlib.sha256(f"{key_id}|{fingerprint}".encode()).hexdigest()
        return f"web_forms:dup:{digest}"

    def claim(self, key_id: UUID, fingerprint: str) -> bool:
        return bool(cache.add(self._cache_key(key_id, fingerprint), 1, DUPLICATE_WINDOW_SECONDS))

    def release(self, key_id: UUID, fingerprint: str) -> None:
        cache.delete(self._cache_key(key_id, fingerprint))


def tenant_slug(tenant_id: UUID) -> str | None:
    """Slug of the company's tenant database; routability is checked by the resolver."""
    slug: str | None = (
        TenantDatabase.objects.using("default")
        .filter(organization_id=tenant_id)
        .values_list("slug", flat=True)
        .first()
    )
    return slug


def department_names(tenant_id: UUID) -> dict[UUID, str]:
    rows = Department.objects.using("default").filter(organization_id=tenant_id)
    return {row.id: row.name for row in rows.only("id", "name")}
