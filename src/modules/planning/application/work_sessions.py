"""Working-session use cases. Every member records their own sessions; the director sees
the company's, a head of sales their department's, everyone else only their own."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal, Scope
from modules.planning.application.ports import DepartmentRoster
from modules.planning.domain.work_session import (
    WorkSession,
    WorkSessionStateError,
    WorkSessionStatus,
)
from shared.application.ports import UnitOfWork
from shared.domain.errors import AccessDeniedError


@dataclass(frozen=True, slots=True)
class WorkSessionFilter:
    user_ids: frozenset[UUID] | None = None  # None = every member (director)
    status: WorkSessionStatus | None = None
    started_from: datetime | None = None
    started_before: datetime | None = None


class WorkSessionRepository(Protocol):
    def add(self, session: WorkSession) -> None: ...

    def open_for_update(self, user_id: UUID) -> WorkSession | None: ...

    def settle(self, session: WorkSession) -> None: ...

    def list(
        self, filters: WorkSessionFilter, *, limit: int, offset: int
    ) -> tuple[list[WorkSession], int]: ...

    def mark_stale_unconfirmed(self, now: datetime) -> int: ...


class WorkSessionService:
    def __init__(
        self,
        repository: WorkSessionRepository,
        roster: DepartmentRoster,
        uow: UnitOfWork,
        policy: AccessPolicy | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repo = repository
        self._roster = roster
        self._uow = uow
        self._policy = policy or AccessPolicy()
        self._now = clock

    def start(self, principal: Principal) -> WorkSession:
        now = self._now()
        with self._uow:
            current = self._repo.open_for_update(principal.user_id)
            if current is not None:
                if not current.is_stale(now):
                    raise WorkSessionStateError("A working session is already open.")
                current.mark_unconfirmed(now)
                self._repo.settle(current)
            session = WorkSession.start(
                user_id=principal.user_id, team_id=principal.department_id, now=now
            )
            self._repo.add(session)  # the database refuses a second open session (race)
        return session

    def stop(self, principal: Principal) -> WorkSession:
        now = self._now()
        with self._uow:
            current = self._repo.open_for_update(principal.user_id)
            if current is None:
                raise WorkSessionStateError("No working session is open.")
            current.stop(now)
            self._repo.settle(current)
        return current

    def current(self, principal: Principal) -> WorkSession | None:
        now = self._now()
        with self._uow:
            session = self._repo.open_for_update(principal.user_id)
            if session is not None and session.is_stale(now):
                session.mark_unconfirmed(now)
                self._repo.settle(session)
                return None
        return session

    def history(
        self,
        principal: Principal,
        *,
        user_id: UUID | None,
        status: WorkSessionStatus | None,
        started_from: datetime | None,
        started_before: datetime | None,
        limit: int,
        offset: int,
    ) -> tuple[list[WorkSession], int]:
        allowed = self._viewable(principal)
        if user_id is not None:
            if allowed is not None and user_id not in allowed:
                raise AccessDeniedError("You cannot see this member's working sessions.")
            users: frozenset[UUID] | None = frozenset({user_id})
        else:
            users = allowed
        return self._repo.list(
            WorkSessionFilter(
                user_ids=users,
                status=status,
                started_from=started_from,
                started_before=started_before,
            ),
            limit=limit,
            offset=offset,
        )

    def expire_stale(self) -> int:
        """System job: sessions nobody stopped become ``unconfirmed`` (no invented end)."""
        with self._uow:
            return self._repo.mark_stale_unconfirmed(self._now())

    def _viewable(self, principal: Principal) -> frozenset[UUID] | None:
        """Whose sessions the caller sees; None = the whole company."""
        own = frozenset({principal.user_id})
        scope = self._policy.scope_for(principal, Permission.TEAM_CABINET_VIEW)
        if scope is None or scope is Scope.OWN:
            return own
        if scope is Scope.ALL:
            return None
        return own | self._roster.members_of(principal.department_id)
