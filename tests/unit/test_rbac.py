from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from modules.access_control.domain.rbac import (
    AccessPolicy,
    Permission,
    Principal,
    ResourceOwnership,
    Role,
    Scope,
)
from shared.domain.errors import AccessDeniedError

policy = AccessPolicy()


def _principal(role: Role, teams: frozenset[UUID] | None = None) -> Principal:
    return Principal(user_id=uuid4(), tenant_id=uuid4(), role=role, team_ids=teams or frozenset())


def test_owner_has_all_scope_everywhere() -> None:
    p = _principal(Role.OWNER)
    for permission in Permission:
        assert policy.scope_for(p, permission) is Scope.ALL


def test_sales_rep_only_own_resources() -> None:
    rep = _principal(Role.SALES_REP)
    own = ResourceOwnership(owner_user_id=rep.user_id)
    foreign = ResourceOwnership(owner_user_id=uuid4())
    assert policy.allows(rep, Permission.CUSTOMERS_UPDATE, own)
    assert not policy.allows(rep, Permission.CUSTOMERS_UPDATE, foreign)
    with pytest.raises(AccessDeniedError):
        policy.require(rep, Permission.CUSTOMERS_UPDATE, foreign)


def test_manager_team_scope() -> None:
    team = uuid4()
    manager = _principal(Role.MANAGER, frozenset({team}))
    assert policy.allows(manager, Permission.CUSTOMERS_VIEW, ResourceOwnership(uuid4(), team))
    assert not policy.allows(
        manager, Permission.CUSTOMERS_VIEW, ResourceOwnership(uuid4(), uuid4())
    )


def test_viewer_cannot_write_and_unknown_permission_denied() -> None:
    viewer = _principal(Role.VIEWER)
    assert policy.allows(viewer, Permission.CUSTOMERS_VIEW)
    assert not policy.allows(viewer, Permission.CUSTOMERS_CREATE)
    assert not policy.allows(_principal(Role.SALES_REP), Permission.MEMBERS_MANAGE)
