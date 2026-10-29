"""Call use cases. RBAC + ownership scope enforced here for every operation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from modules.access_control.domain.rbac import (
    AccessPolicy,
    Permission,
    Principal,
    ResourceOwnership,
    Scope,
)
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.telephony.application.ports import (
    CallDialer,
    CallFilter,
    CallRepository,
    CallVisibility,
    SubjectChecker,
)
from modules.telephony.domain.call import (
    Call,
    CallDirection,
    CallLink,
    CallStatus,
)
from platform_core.application.ports import EventOutbox
from shared.application.ports import UnitOfWork
from shared.domain.errors import NotFoundError
from shared.domain.events import DomainEvent

AGGREGATE = "call"


@dataclass(frozen=True, slots=True)
class LogCall:
    direction: CallDirection
    from_number: str
    to_number: str
    status: CallStatus
    started_at: datetime
    duration_seconds: int = 0
    recording_url: str | None = None
    external_id: str | None = None
    link: CallLink | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class RequestCall:
    from_number: str
    to_number: str
    link: CallLink | None = None


@dataclass(frozen=True, slots=True)
class CallPage:
    items: list[Call]
    total: int


def _ownership(call: Call) -> ResourceOwnership:
    return ResourceOwnership(owner_user_id=call.owner_user_id, team_id=call.team_id)


class CallService:
    def __init__(
        self,
        repository: CallRepository,
        subjects: SubjectChecker,
        dialer: CallDialer,
        uow: UnitOfWork,
        outbox: EventOutbox,
        audit: AuditLog,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._repo = repository
        self._subjects = subjects
        self._dialer = dialer
        self._uow = uow
        self._outbox = outbox
        self._audit = audit
        self._policy = policy or AccessPolicy()

    def get(self, principal: Principal, call_id: UUID) -> Call:
        call = self._repo.get(call_id)
        if call is None or not self._policy.allows(
            principal, Permission.TELEPHONY_VIEW, _ownership(call)
        ):
            raise NotFoundError("Call not found.")
        return call

    def list_calls(
        self, principal: Principal, filters: CallFilter, *, limit: int, offset: int
    ) -> CallPage:
        scope = self._policy.require(principal, Permission.TELEPHONY_VIEW)
        visibility = self._visibility(principal, scope)
        return CallPage(
            items=self._repo.list_visible(visibility, filters, limit=limit, offset=offset),
            total=self._repo.count(visibility, filters),
        )

    def _visibility(self, principal: Principal, scope: Scope) -> CallVisibility:
        if scope is Scope.ALL:
            return CallVisibility()
        if scope is Scope.TEAM:
            return CallVisibility(owner_user_id=principal.user_id, team_ids=principal.team_ids)
        return CallVisibility(owner_user_id=principal.user_id)

    def log_call(self, principal: Principal, command: LogCall) -> Call:
        self._policy.require(principal, Permission.TELEPHONY_CREATE)
        with self._uow:
            self._check_link(command.link)
            call, event = Call.log(
                direction=command.direction,
                from_number=command.from_number,
                to_number=command.to_number,
                owner_user_id=principal.user_id,
                team_id=principal.department_id,
                status=command.status,
                started_at=command.started_at,
                duration_seconds=command.duration_seconds,
                recording_url=command.recording_url,
                external_id=command.external_id,
                link=command.link,
                notes=command.notes,
            )
            self._repo.add(call)
            self._emit(event, call)
            self._record(principal, "call.logged", call)
        return call

    def request_call(self, principal: Principal, command: RequestCall) -> Call:
        self._policy.require(principal, Permission.TELEPHONY_CREATE)
        with self._uow:
            self._check_link(command.link)
            call, event = Call.request(
                from_number=command.from_number,
                to_number=command.to_number,
                owner_user_id=principal.user_id,
                team_id=principal.department_id,
                link=command.link,
            )
            external_id = self._dialer.dial(
                from_number=str(call.from_number), to_number=str(call.to_number)
            )
            call.set_external_id(external_id)
            self._repo.add(call)
            self._emit(event, call)
            self._record(principal, "call.requested", call)
        return call

    def advance(self, principal: Principal, call_id: UUID, status: CallStatus) -> Call:
        with self._uow:
            call = self._load_for_write(principal, call_id)
            version = call.version
            call.advance(status)
            self._repo.save(call, expected_version=version)
            self._record(principal, "call.advanced", call)
        return call

    def end(
        self,
        principal: Principal,
        call_id: UUID,
        status: CallStatus,
        *,
        duration_seconds: int = 0,
        recording_url: str | None = None,
    ) -> Call:
        with self._uow:
            call = self._load_for_write(principal, call_id)
            version = call.version
            event = call.end(status, duration_seconds=duration_seconds, recording_url=recording_url)
            self._repo.save(call, expected_version=version)
            self._emit(event, call)
            self._record(principal, "call.ended", call)
        return call

    def _check_link(self, link: CallLink | None) -> None:
        if link is not None and not self._subjects.exists(link.type, link.id):
            raise NotFoundError(f"The {link.type.value} this call links to was not found.")

    def _load_for_write(self, principal: Principal, call_id: UUID) -> Call:
        call = self.get(principal, call_id)
        self._policy.require(principal, Permission.TELEPHONY_UPDATE, _ownership(call))
        return call

    def _emit(self, event: DomainEvent, call: Call) -> None:
        self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(call.id))

    def _record(self, principal: Principal, action: str, call: Call) -> None:
        self._audit.record(
            AuditEntry(
                actor_user_id=principal.user_id,
                action=action,
                resource_type=AGGREGATE,
                resource_id=str(call.id),
            )
        )
