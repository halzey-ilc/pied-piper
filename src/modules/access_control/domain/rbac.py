"""Role-based access control with ownership scopes.

A role grants a permission with a scope:

* ``ALL``  - any resource in the tenant;
* ``TEAM`` - resources owned by the principal or assigned to one of its teams;
* ``OWN``  - resources owned by the principal only.

The policy is pure Python and is evaluated inside use cases, not in views.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from uuid import UUID

from shared.domain.errors import AccessDeniedError


class Permission(StrEnum):
    CUSTOMERS_VIEW = "customers.view"
    CUSTOMERS_CREATE = "customers.create"
    CUSTOMERS_UPDATE = "customers.update"
    CUSTOMERS_DELETE = "customers.delete"
    LEADS_VIEW = "leads.view"
    LEADS_CREATE = "leads.create"
    LEADS_UPDATE = "leads.update"
    LEADS_CONVERT = "leads.convert"
    PIPELINES_VIEW = "pipelines.view"
    PIPELINES_MANAGE = "pipelines.manage"
    DEALS_VIEW = "deals.view"
    DEALS_CREATE = "deals.create"
    DEALS_UPDATE = "deals.update"
    ACTIVITIES_VIEW = "activities.view"
    ACTIVITIES_CREATE = "activities.create"
    ACTIVITIES_UPDATE = "activities.update"
    MEETINGS_VIEW = "meetings.view"
    MEETINGS_CREATE = "meetings.create"
    MEETINGS_UPDATE = "meetings.update"
    REPORTS_VIEW = "reports.view"
    REPORTS_GENERATE = "reports.generate"
    MEMBERS_MANAGE = "members.manage"
    INTEGRATIONS_MANAGE = "integrations.manage"


class Scope(IntEnum):
    OWN = 1
    TEAM = 2
    ALL = 3


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MANAGER = "manager"
    SALES_REP = "sales_rep"
    VIEWER = "viewer"


_ALL = Scope.ALL
ROLE_GRANTS: Mapping[Role, Mapping[Permission, Scope]] = {
    Role.OWNER: dict.fromkeys(Permission, _ALL),
    Role.ADMIN: dict.fromkeys(Permission, _ALL),
    Role.MANAGER: {
        Permission.CUSTOMERS_VIEW: Scope.TEAM,
        Permission.CUSTOMERS_CREATE: Scope.TEAM,
        Permission.CUSTOMERS_UPDATE: Scope.TEAM,
        Permission.LEADS_VIEW: Scope.TEAM,
        Permission.LEADS_CREATE: Scope.TEAM,
        Permission.LEADS_UPDATE: Scope.TEAM,
        Permission.LEADS_CONVERT: Scope.TEAM,
        Permission.PIPELINES_VIEW: Scope.ALL,
        Permission.PIPELINES_MANAGE: Scope.ALL,
        Permission.DEALS_VIEW: Scope.TEAM,
        Permission.DEALS_CREATE: Scope.TEAM,
        Permission.DEALS_UPDATE: Scope.TEAM,
        Permission.ACTIVITIES_VIEW: Scope.TEAM,
        Permission.ACTIVITIES_CREATE: Scope.TEAM,
        Permission.ACTIVITIES_UPDATE: Scope.TEAM,
        Permission.MEETINGS_VIEW: Scope.TEAM,
        Permission.MEETINGS_CREATE: Scope.TEAM,
        Permission.MEETINGS_UPDATE: Scope.TEAM,
        Permission.REPORTS_VIEW: Scope.ALL,
        Permission.REPORTS_GENERATE: Scope.ALL,
    },
    Role.SALES_REP: {
        Permission.CUSTOMERS_VIEW: Scope.OWN,
        Permission.CUSTOMERS_CREATE: Scope.OWN,
        Permission.CUSTOMERS_UPDATE: Scope.OWN,
        Permission.LEADS_VIEW: Scope.OWN,
        Permission.LEADS_CREATE: Scope.OWN,
        Permission.LEADS_UPDATE: Scope.OWN,
        Permission.LEADS_CONVERT: Scope.OWN,
        Permission.PIPELINES_VIEW: Scope.ALL,
        Permission.DEALS_VIEW: Scope.OWN,
        Permission.DEALS_CREATE: Scope.OWN,
        Permission.DEALS_UPDATE: Scope.OWN,
        Permission.ACTIVITIES_VIEW: Scope.OWN,
        Permission.ACTIVITIES_CREATE: Scope.OWN,
        Permission.ACTIVITIES_UPDATE: Scope.OWN,
        Permission.MEETINGS_VIEW: Scope.OWN,
        Permission.MEETINGS_CREATE: Scope.OWN,
        Permission.MEETINGS_UPDATE: Scope.OWN,
    },
    Role.VIEWER: {
        Permission.CUSTOMERS_VIEW: Scope.ALL,
        Permission.LEADS_VIEW: Scope.ALL,
        Permission.PIPELINES_VIEW: Scope.ALL,
        Permission.DEALS_VIEW: Scope.ALL,
        Permission.ACTIVITIES_VIEW: Scope.ALL,
        Permission.MEETINGS_VIEW: Scope.ALL,
        Permission.REPORTS_VIEW: Scope.ALL,
    },
}


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: UUID
    tenant_id: UUID
    role: Role
    team_ids: frozenset[UUID] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class ResourceOwnership:
    owner_user_id: UUID | None
    team_id: UUID | None = None


class AccessPolicy:
    def __init__(self, grants: Mapping[Role, Mapping[Permission, Scope]] = ROLE_GRANTS) -> None:
        self._grants = grants

    def scope_for(self, principal: Principal, permission: Permission) -> Scope | None:
        return self._grants.get(principal.role, {}).get(permission)

    def allows(
        self,
        principal: Principal,
        permission: Permission,
        resource: ResourceOwnership | None = None,
    ) -> bool:
        scope = self.scope_for(principal, permission)
        if scope is None:
            return False
        if resource is None or scope is Scope.ALL:
            return True
        if resource.owner_user_id == principal.user_id:
            return True
        return scope is Scope.TEAM and resource.team_id in principal.team_ids

    def require(
        self,
        principal: Principal,
        permission: Permission,
        resource: ResourceOwnership | None = None,
    ) -> Scope:
        if not self.allows(principal, permission, resource):
            raise AccessDeniedError("You do not have permission to perform this action.")
        scope = self.scope_for(principal, permission)
        if scope is None:  # pragma: no cover - guarded by allows()
            raise AccessDeniedError("You do not have permission to perform this action.")
        return scope
